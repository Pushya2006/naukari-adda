"""
Rule-based resume <-> job skill matching.

This module is the single source of truth for:
  * normalising a skill string (case / spacing / punctuation / aliases)
  * extracting known skills out of free-form resume text
  * comparing a candidate's skills against a job's required skills

It is deliberately dependency-free so that it always works, even when the
Anthropic API is unavailable (see jobs/ai_analysis.py).
"""

import re

# ---------------------------------------------------------------------------
# Canonical skill vocabulary
# ---------------------------------------------------------------------------
# Key   = canonical display name
# Value = list of aliases / spellings that should map to the canonical name.
#         The canonical name itself is always matched too.
SKILL_ALIASES = {
    'Python': ['python3', 'python 3', 'py'],
    'Django': ['django rest framework', 'drf', 'django-rest-framework'],
    'Flask': [],
    'FastAPI': ['fast api'],
    'SQL': ['structured query language'],
    'MySQL': ['my sql'],
    'PostgreSQL': ['postgres', 'postgre sql', 'psql'],
    'SQLite': ['sqlite3'],
    'MongoDB': ['mongo db', 'mongo'],
    'Oracle': ['oracle db', 'pl/sql', 'plsql'],
    'Database Design': ['db design', 'schema design', 'data modelling',
                        'data modeling'],
    'Query Optimization': ['query optimisation', 'query tuning',
                           'sql tuning'],
    'HTML': ['html5'],
    'CSS': ['css3'],
    'JavaScript': ['java script', 'js', 'es6', 'ecmascript'],
    'TypeScript': ['type script', 'ts'],
    'React': ['react.js', 'reactjs', 'react js'],
    'Angular': ['angular.js', 'angularjs'],
    'Vue': ['vue.js', 'vuejs'],
    'Node.js': ['node js', 'nodejs', 'node'],
    'Express': ['express.js', 'expressjs'],
    'Bootstrap': ['bootstrap5', 'bootstrap 5'],
    'jQuery': ['jquery'],
    'Java': [],
    'C': [],
    'C++': ['cpp', 'c plus plus'],
    'C#': ['c sharp', 'csharp', '.net', 'dotnet'],
    'Go': ['golang'],
    'PHP': [],
    'Ruby': ['ruby on rails', 'rails'],
    'R': [],
    'Git': ['github', 'gitlab', 'version control'],
    'Docker': ['containerisation', 'containerization'],
    'Kubernetes': ['k8s'],
    'Jenkins': ['ci/cd', 'cicd', 'ci cd'],
    'Linux': ['unix', 'bash', 'shell scripting'],
    'AWS': ['amazon web services', 'ec2', 's3'],
    'Azure': ['microsoft azure'],
    'GCP': ['google cloud', 'google cloud platform'],
    'Terraform': ['infrastructure as code'],
    'Ansible': [],
    'DevOps': ['dev ops'],
    'Machine Learning': ['ml', 'deep learning', 'scikit-learn', 'sklearn'],
    'Data Analysis': ['data analytics', 'data analyst'],
    'Pandas': [],
    'NumPy': ['numpy'],
    'Power BI': ['powerbi', 'power-bi'],
    'Tableau': [],
    'Excel': ['ms excel', 'microsoft excel', 'advanced excel'],
    'REST API': ['rest apis', 'restful api', 'restful apis', 'rest',
                 'api development'],
    'GraphQL': ['graph ql'],
    'Testing': ['unit testing', 'pytest', 'junit', 'selenium', 'qa',
                'test automation', 'manual testing'],
    'Agile': ['scrum', 'kanban'],
    'Data Structures': ['dsa', 'data structures and algorithms',
                        'algorithms'],
    'OOP': ['object oriented programming', 'object-oriented programming'],
    'Redis': [],
    'Kafka': ['apache kafka'],
    'Spark': ['apache spark', 'pyspark'],
    'Hadoop': [],
    'Communication': ['communication skills'],
    'Problem Solving': ['problem-solving'],
}

# Skills whose canonical form is short/ambiguous ("R", "C", "Go") need a
# stricter match so that the letter "r" inside a word is not a hit.
_SHORT_SKILLS = {'c', 'r', 'go', 'js', 'ts', 'ml', 'qa', 'sql', 'aws', 'php'}


def _clean(value):
    """Lower-case, collapse whitespace, drop decorative punctuation."""
    if not value:
        return ''
    text = str(value).lower()
    # Normalise common separators to a single space, but keep + # . /
    # because they are part of real skill names (C++, C#, Node.js, CI/CD).
    text = re.sub(r'[\u2022\u2023\u25cf\|\t\r\n]+', ' ', text)
    text = re.sub(r'[^a-z0-9\+\#\.\/\- ]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# Pre-built lookup: cleaned alias -> canonical name
_LOOKUP = {}
for _canonical, _aliases in SKILL_ALIASES.items():
    _LOOKUP[_clean(_canonical)] = _canonical
    for _alias in _aliases:
        _LOOKUP[_clean(_alias)] = _canonical


def canonicalize(raw_skill):
    """
    Map an arbitrary skill string to its canonical name.

    '  python3 ' -> 'Python'
    'REACT.JS'   -> 'React'
    'power bi'   -> 'Power BI'

    Unknown skills are returned title-cased so they can still be compared
    consistently between a resume and a job posting.
    """
    cleaned = _clean(raw_skill)
    if not cleaned:
        return ''
    if cleaned in _LOOKUP:
        return _LOOKUP[cleaned]
    # Trailing plurals / possessives, e.g. "apis" -> "api"
    singular = re.sub(r's$', '', cleaned)
    if singular in _LOOKUP:
        return _LOOKUP[singular]
    return raw_skill.strip().strip('.,;:').title()


def parse_skill_list(raw):
    """
    Turn a stored skill string into an ordered, de-duplicated list of
    canonical skills.

    Accepts comma, semicolon, slash, pipe or newline separated values:
        'SQL , Python, power bi '  -> ['SQL', 'Python', 'Power BI']
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        parts = re.split(r'[,;\n\r\|]+', str(raw))

    out = []
    for part in parts:
        candidates = [part]

        # "django/react" is two skills, but "CI/CD" and "and/or" style names
        # are one. Only split on '/' when the whole token is not a skill we
        # recognise and both halves look like words.
        if '/' in part and _clean(part) not in _LOOKUP:
            halves = [half for half in part.split('/') if half.strip()]
            if len(halves) > 1 and all(len(h.strip()) > 2 for h in halves):
                candidates = halves

        for candidate in candidates:
            skill = canonicalize(candidate)
            if skill and skill not in out:
                out.append(skill)

    return out


def extract_skills_from_text(text):
    """
    Scan free-form resume text and return the canonical skills found.

    Uses word-boundary matching so that 'Java' does not match 'JavaScript'
    and 'R' does not match every word containing the letter r.
    """
    if not text:
        return []

    cleaned = _clean(text)
    found = []

    for canonical, aliases in SKILL_ALIASES.items():
        candidates = [canonical] + list(aliases)
        for candidate in candidates:
            needle = _clean(candidate)
            if not needle:
                continue
            # \b does not work next to +, # or . so build the boundary by hand.
            pattern = r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])'
            if needle in _SHORT_SKILLS and len(needle) <= 2:
                # Extra-strict for 1-2 char skills: must be standalone token.
                pattern = r'(?<![a-z0-9\+\#\.])' + re.escape(needle) + \
                          r'(?![a-z0-9\+\#\.])'
            if re.search(pattern, cleaned):
                if canonical not in found:
                    found.append(canonical)
                break

    return found


def get_candidate_skills(profile):
    """
    Best-effort list of a student's skills.

    Prefers the skills saved at upload time, and falls back to re-scanning
    the stored resume text. This keeps old profiles (saved before the
    `skills` field existed) working without a data migration.
    """
    if profile is None:
        return []

    saved = parse_skill_list(getattr(profile, 'skills', '') or '')
    from_text = extract_skills_from_text(getattr(profile, 'resume_text', '') or '')

    merged = list(saved)
    for skill in from_text:
        if skill not in merged:
            merged.append(skill)
    return merged


def match_skills(candidate_skills, required_skills, resume_text=''):
    """
    Compare a candidate against a job's requirements.

    Returns a dict:
        {
            'required':   [...],   # canonical required skills
            'candidate':  [...],   # canonical candidate skills
            'matched':    [...],
            'missing':    [...],
            'percentage': 0-100,
            'has_requirements': bool,
        }

    A required skill counts as matched if the candidate lists it OR if it
    appears in the raw resume text (covers skills mentioned only inside a
    project description).
    """
    required = parse_skill_list(required_skills)
    candidate = parse_skill_list(candidate_skills)

    text_skills = extract_skills_from_text(resume_text) if resume_text else []
    lowered_text = _clean(resume_text)

    matched, missing = [], []

    for skill in required:
        hit = skill in candidate or skill in text_skills

        if not hit and lowered_text:
            # Final chance: the exact required phrase appears in the resume
            # even though it is not in our vocabulary (e.g. a niche tool).
            needle = _clean(skill)
            if needle:
                pattern = r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])'
                hit = bool(re.search(pattern, lowered_text))

        (matched if hit else missing).append(skill)

    if required:
        percentage = int(round(len(matched) / len(required) * 100))
    else:
        percentage = 0

    return {
        'required': required,
        'candidate': candidate,
        'matched': matched,
        'missing': missing,
        'percentage': percentage,
        'has_requirements': bool(required),
    }


def match_profile_to_job(profile, job):
    """Convenience wrapper: StudentProfile + Job -> match dict."""
    resume_text = getattr(profile, 'resume_text', '') or ''
    return match_skills(
        candidate_skills=get_candidate_skills(profile),
        required_skills=getattr(job, 'required_skills', '') or '',
        resume_text=resume_text,
    )
