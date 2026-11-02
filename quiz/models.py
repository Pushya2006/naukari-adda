from django.contrib.auth import get_user_model
from django.db import models

from jobs.models import Job

User = get_user_model()


class QuizAttempt(models.Model):
    """One scored attempt. One row per (user, job) - enforced below."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    score = models.IntegerField()

    total = models.IntegerField()

    attempted_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ['-attempted_at']

    def __str__(self):
        return f"{self.user.username} - {self.score}/{self.total}"

    @property
    def percentage(self):
        if not self.total:
            return 0
        return int(round(self.score / self.total * 100))


class Question(models.Model):

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

    text = models.CharField(
        max_length=255
    )

    option1 = models.CharField(max_length=100)
    option2 = models.CharField(max_length=100)
    option3 = models.CharField(max_length=100)
    option4 = models.CharField(max_length=100)

    correct_option = models.IntegerField(
        choices=[
            (1, 'Option 1'),
            (2, 'Option 2'),
            (3, 'Option 3'),
            (4, 'Option 4'),
        ]
    )

    class Meta:
        ordering = ['role', 'id']

    def __str__(self):
        return f"[{self.role}] {self.text}"


class QuizAnswer(models.Model):
    """An individual answer, kept for review and analytics."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE
    )

    selected_option = models.IntegerField()

    is_correct = models.BooleanField()

    answered_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        unique_together = ('user', 'question')
        ordering = ['-answered_at']

    def __str__(self):
        return f"{self.user.username} - Q{self.question_id}"
