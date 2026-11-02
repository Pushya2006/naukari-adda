from django.contrib import admin

from .models import Question, QuizAnswer, QuizAttempt


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('text', 'role', 'correct_option')
    list_filter = ('role',)
    search_fields = ('text',)


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'job', 'score', 'total', 'attempted_at')
    list_filter = ('job',)
    search_fields = ('user__username',)


@admin.register(QuizAnswer)
class QuizAnswerAdmin(admin.ModelAdmin):
    list_display = ('user', 'question', 'selected_option', 'is_correct')
    list_filter = ('is_correct',)
