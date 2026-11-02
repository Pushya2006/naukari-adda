from django.db import models

from users.models import CustomUser


class Job(models.Model):

    title = models.CharField(
        max_length=100
    )

    company = models.CharField(
        max_length=100
    )

    description = models.TextField()

    # Role drives which quiz question bank is used.
    ROLE_CHOICES = (
        ('Python', 'Python'),
        ('Frontend', 'Frontend'),
        ('Data Analyst', 'Data Analyst'),
        ('DevOps', 'DevOps'),
    )

    role = models.CharField(
        max_length=50,
        choices=ROLE_CHOICES,
        default='Python'
    )

    required_skills = models.TextField(
        blank=True,
        null=True,
        help_text='Comma separated, e.g. Python, Django, SQL'
    )

    posted_by = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE
    )

    # Quiz eligibility threshold.
    min_score_required = models.IntegerField(
        default=3
    )

    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.title

    @property
    def required_skill_list(self):
        from .matching import parse_skill_list
        return parse_skill_list(self.required_skills or '')


class Application(models.Model):

    # Canonical application lifecycle. Kept as plain strings so that the
    # existing rows ('Applied', 'Eligible', 'Rejected', ...) stay valid.
    STATUS_APPLIED = 'Applied'
    STATUS_QUIZ_PENDING = 'Quiz Pending'
    STATUS_ELIGIBLE = 'Eligible'
    STATUS_NOT_ELIGIBLE = 'Not Eligible'
    STATUS_SHORTLISTED = 'Shortlisted'
    STATUS_INTERVIEW = 'Interview Scheduled'
    STATUS_SELECTED = 'Selected'
    STATUS_REJECTED = 'Rejected'

    STATUS_CHOICES = (
        (STATUS_QUIZ_PENDING, 'Quiz Pending'),
        (STATUS_ELIGIBLE, 'Eligible'),
        (STATUS_NOT_ELIGIBLE, 'Not Eligible'),
        (STATUS_APPLIED, 'Applied'),
        (STATUS_SHORTLISTED, 'Shortlisted'),
        (STATUS_INTERVIEW, 'Interview Scheduled'),
        (STATUS_SELECTED, 'Selected'),
        (STATUS_REJECTED, 'Rejected'),
    )

    # Statuses a recruiter is allowed to set by hand.
    RECRUITER_SETTABLE = (
        STATUS_SHORTLISTED,
        STATUS_INTERVIEW,
        STATUS_SELECTED,
        STATUS_REJECTED,
    )

    # Order used to draw the student-facing progress tracker.
    PROGRESS_ORDER = (
        STATUS_QUIZ_PENDING,
        STATUS_ELIGIBLE,
        STATUS_APPLIED,
        STATUS_SHORTLISTED,
        STATUS_INTERVIEW,
        STATUS_SELECTED,
    )

    BADGE_CLASSES = {
        STATUS_SELECTED: 'bg-success',
        STATUS_REJECTED: 'bg-danger',
        STATUS_INTERVIEW: 'bg-info text-dark',
        STATUS_SHORTLISTED: 'bg-primary',
        STATUS_APPLIED: 'bg-primary',
        STATUS_ELIGIBLE: 'bg-warning text-dark',
        STATUS_NOT_ELIGIBLE: 'bg-danger',
        STATUS_QUIZ_PENDING: 'bg-secondary',
    }

    student = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE
    )

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE
    )

    applied_at = models.DateTimeField(
        auto_now_add=True
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_QUIZ_PENDING,
    )

    status_updated_at = models.DateTimeField(auto_now=True)

    # Interview details.
    interview_date = models.DateField(
        null=True,
        blank=True
    )

    interview_time = models.TimeField(
        null=True,
        blank=True
    )

    meeting_link = models.URLField(
        null=True,
        blank=True
    )

    class Meta:
        unique_together = ('student', 'job')
        ordering = ['-applied_at']

    def __str__(self):
        return f"{self.student.username} applied to {self.job.title}"

    @property
    def badge_class(self):
        return self.BADGE_CLASSES.get(self.status, 'bg-secondary')

    @property
    def is_closed(self):
        return self.status in (self.STATUS_REJECTED, self.STATUS_SELECTED)

    @property
    def progress_percentage(self):
        """0-100 for the student-facing status bar."""
        if self.status == self.STATUS_REJECTED:
            return 100
        if self.status == self.STATUS_NOT_ELIGIBLE:
            return 100
        if self.status in self.PROGRESS_ORDER:
            index = self.PROGRESS_ORDER.index(self.status)
            return int(round((index + 1) / len(self.PROGRESS_ORDER) * 100))
        return 0


class Notification(models.Model):

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE
    )

    message = models.TextField()

    is_read = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.user.username
