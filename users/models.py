from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):

    USER_TYPE_CHOICES = (
        ('student', 'Student'),
        ('recruiter', 'Recruiter'),
        ('admin', 'Admin'),
    )

    user_type = models.CharField(
        max_length=10,
        choices=USER_TYPE_CHOICES,
        default='student',
    )

    @property
    def is_student(self):
        return self.user_type == 'student'

    @property
    def is_recruiter(self):
        return self.user_type == 'recruiter'


class StudentProfile(models.Model):

    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE
    )

    resume = models.FileField(
        upload_to='resumes/',
        null=True,
        blank=True
    )

    # Raw text pulled out of the resume by pdfplumber / OCR.
    resume_text = models.TextField(
        blank=True,
        null=True
    )

    # Comma-separated canonical skills detected from the resume, plus
    # anything the student adds by hand. This field is what the matching
    # engine reads - it previously existed only in code, never in the DB,
    # which is why every match showed 0%.
    skills = models.TextField(
        blank=True,
        default='',
        help_text='Comma separated, e.g. Python, Django, SQL'
    )

    # ---- Candidate profile ------------------------------------------------
    full_name = models.CharField(max_length=120, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    headline = models.CharField(
        max_length=150,
        blank=True,
        default='',
        help_text='e.g. Final year CSE student | Django developer'
    )
    education = models.TextField(blank=True, default='')
    projects = models.TextField(
        blank=True,
        default='',
        help_text='One project per line'
    )
    certifications = models.TextField(
        blank=True,
        default='',
        help_text='One certification per line'
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Student profile'
        verbose_name_plural = 'Student profiles'

    def __str__(self):
        return self.full_name or self.user.username

    # ---- Derived helpers --------------------------------------------------

    @property
    def skill_list(self):
        """Canonical skills as a list (safe to use in templates)."""
        from jobs.matching import get_candidate_skills
        return get_candidate_skills(self)

    @property
    def project_list(self):
        return [line.strip() for line in (self.projects or '').splitlines()
                if line.strip()]

    @property
    def certification_list(self):
        return [line.strip() for line in
                (self.certifications or '').splitlines() if line.strip()]

    def completion_breakdown(self):
        """
        Which parts of the profile are done. Used for the completion bar so
        the student can see exactly what is missing.
        """
        return [
            ('Email address', bool((self.user.email or '').strip())),
            ('Full name', bool(self.full_name.strip())),
            ('Phone number', bool(self.phone.strip())),
            ('Headline', bool(self.headline.strip())),
            ('Resume uploaded', bool(self.resume)),
            ('Resume text extracted', bool((self.resume_text or '').strip())),
            ('Skills', bool(self.skill_list)),
            ('Education', bool(self.education.strip())),
            ('Projects', bool(self.project_list)),
            ('Certifications', bool(self.certification_list)),
        ]

    @property
    def completion_percentage(self):
        items = self.completion_breakdown()
        if not items:
            return 0
        done = sum(1 for _, ok in items if ok)
        return int(round(done / len(items) * 100))

    @property
    def missing_profile_items(self):
        return [label for label, ok in self.completion_breakdown() if not ok]
