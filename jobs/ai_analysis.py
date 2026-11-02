"""
Local, deterministic resume <-> job <-> quiz analysis.

This module used to call the Anthropic API for the candidate match score.
It no longer does: the score is now computed entirely from data already in
the database (resume/skill match, job requirements, quiz performance), so
it:
  * never depends on an API key, external network access, or any AI
    provider being reachable,
  * always returns the exact same score for the exact same inputs
    (fully explainable - every number can be traced back to a formula),
  * keeps working identically whether or not `anthropic` is installed.

Design rules kept from before:
  * Results are cached in Django's cache (not the database) so no schema
    change was needed and repeated page loads recompute nothing extra.
  * Only resume/job data already available in this app is used - nothing
    is sent anywhere.
"""

import hashlib
import logging
import re

from django.core.cache import cache

from .matching import match_profile_to_job

logger = logging.getLogger(__name__)

# How long a computed analysis stays cached (seconds). Purely a minor
# performance optimisation now - recomputing is cheap and local - but kept
# so the existing "re-run analysis" refresh links still do something.
CACHE_TTL = 60 * 60 * 6  # 6 hours

# --- Scoring weights ---------------------------------------------------
# Three factors, as requested: resume/skill match, job compatibility, and
# quiz performance. When the quiz hasn't been attempted, its weight is
# dropped and the remaining two are renormalised so they still sum to
# 100% - the overall score is never dragged down or zeroed just because
# the quiz is missing.
RESUME_WEIGHT = 0.5
COMPATIBILITY_WEIGHT = 0.2
QUIZ_WEIGHT = 0.3


def _strip_personal_information(text):
    """
    Remove obvious direct identifiers (email, phone, social profile URLs)
    from resume text. Kept as a small, independently useful utility even
    though the analyzer itself no longer sends resume text anywhere.
    """
    if not text:
        return ''

    text = re.sub(r'[\w\.\-\+]+@[\w\-]+\.[\w\.\-]+', '[email]', text)
    text = re.sub(r'(?<!\d)(?:\+?\d[\d\s\-\(\)]{8,}\d)(?!\d)', '[phone]', text)
    text = re.sub(r'https?://\S*(linkedin|github|facebook|instagram|twitter)\S*',
                  '[profile-url]', text, flags=re.I)
    return text


def _cache_key(resume_text, job, quiz_signature):
    raw = '{}|{}|{}|{}|{}'.format(
        job.pk,
        getattr(job, 'required_skills', '') or '',
        getattr(job, 'description', '') or '',
        resume_text or '',
        quiz_signature,
    )
    digest = hashlib.sha256(raw.encode('utf-8', 'ignore')).hexdigest()
    return 'local_match:{}'.format(digest)


def _get_quiz_result(profile, job):
    """(score, total) for this candidate's quiz attempt on this job, or
    None if they haven't attempted it. Imported lazily to avoid a
    module-load-time dependency between the jobs and quiz apps."""
    if profile is None or getattr(profile, 'user_id', None) is None:
        return None

    from quiz.models import QuizAttempt

    attempt = QuizAttempt.objects.filter(
        user_id=profile.user_id, job=job).first()
    if attempt is None:
        return None
    return attempt.score, attempt.total


def _compute_scores(profile, job):
    """
    The three-factor deterministic score.

    - Resume/Skill Match: % of the job's required skills found on the
      candidate's resume (existing matching engine, unchanged).
    - Job Compatibility: % of the candidate's OWN skills that are
      relevant to this job - a distinct signal from the recall-style
      resume match above (a candidate whose few listed skills are all
      relevant to the role is more "compatible" than one whose skills
      are mostly unrelated, even at the same raw match percentage).
    - Quiz Performance: the candidate's actual quiz score for this job,
      only when they have attempted it.
    """
    resume_result = match_profile_to_job(profile, job)
    resume_pct = resume_result['percentage']

    candidate_skills = resume_result['candidate']
    matched_skills = resume_result['matched']

    if candidate_skills:
        compatibility_pct = int(round(
            len(matched_skills) / len(candidate_skills) * 100))
    else:
        compatibility_pct = resume_pct

    quiz_info = _get_quiz_result(profile, job)
    quiz_pct = None
    if quiz_info is not None:
        score, total = quiz_info
        quiz_pct = int(round(score / total * 100)) if total else 0

    weights = {'resume': RESUME_WEIGHT, 'compatibility': COMPATIBILITY_WEIGHT}
    values = {'resume': resume_pct, 'compatibility': compatibility_pct}
    if quiz_pct is not None:
        weights['quiz'] = QUIZ_WEIGHT
        values['quiz'] = quiz_pct

    total_weight = sum(weights.values())
    overall_pct = int(round(
        sum(weights[k] * values[k] for k in weights) / total_weight
    )) if total_weight else 0

    return {
        'resume_result': resume_result,
        'resume_percentage': resume_pct,
        'compatibility_percentage': compatibility_pct,
        'quiz_percentage': quiz_pct,
        'overall_percentage': overall_pct,
    }


def _build_explanation(scores):
    """Plain-language, fully traceable breakdown of the overall score."""

    parts = [
        f"Resume Match: {scores['resume_percentage']}% "
        f"(required job skills found on the resume).",
        f"Job Compatibility: {scores['compatibility_percentage']}% "
        f"(share of the candidate's own skills relevant to this job).",
    ]

    if scores['quiz_percentage'] is not None:
        parts.append(f"Quiz Score: {scores['quiz_percentage']}%.")
    else:
        parts.append("Quiz Score: Not Attempted.")

    if scores['quiz_percentage'] is not None:
        parts.append(
            f"Overall Match: {scores['overall_percentage']}%, weighted "
            f"from resume match, job compatibility and quiz performance."
        )
    else:
        parts.append(
            f"Overall Match: {scores['overall_percentage']}%, based on "
            f"resume match and job compatibility (quiz not yet attempted)."
        )

    return ' '.join(parts)


def analyze_resume_for_job(profile, job, use_cache=True):
    """
    Return a structured, deterministic analysis dict for a candidate/job
    pair. Always returns a dict, always computed locally - there is no
    failure mode where this needs to "fall back" to anything, since there
    is nothing external for it to depend on.
    """
    resume_text = (getattr(profile, 'resume_text', '') or '').strip()

    quiz_info = _get_quiz_result(profile, job)
    quiz_signature = f'{quiz_info[0]}/{quiz_info[1]}' if quiz_info else 'none'

    key = _cache_key(resume_text, job, quiz_signature)
    if use_cache:
        cached = cache.get(key)
        if cached:
            return cached

    scores = _compute_scores(profile, job)
    resume_result = scores['resume_result']

    result = {
        'source': 'local',
        'unavailable_reason': '',
        'detected_skills': resume_result['candidate'],
        'matched_skills': resume_result['matched'],
        'missing_skills': resume_result['missing'],
        'strengths': resume_result['matched'][:5],
        'match_percentage': scores['overall_percentage'],
        'resume_percentage': scores['resume_percentage'],
        'compatibility_percentage': scores['compatibility_percentage'],
        'quiz_percentage': scores['quiz_percentage'],
        'quiz_attempted': quiz_info is not None,
        'explanation': _build_explanation(scores),
        'suggestions': [
            'Add {} to your resume if you have used it.'.format(skill)
            for skill in resume_result['missing'][:5]
        ],
        'required_skills': resume_result['required'],
    }

    if use_cache:
        cache.set(key, result, CACHE_TTL)

    return result
