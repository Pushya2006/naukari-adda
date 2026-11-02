from django.contrib import admin

from .models import Application, Job, Notification


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ('title', 'company', 'role', 'min_score_required',
                    'posted_by')
    list_filter = ('role', 'company')
    search_fields = ('title', 'company', 'required_skills', 'description')


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ('student', 'job', 'status', 'applied_at',
                    'interview_date')
    list_filter = ('status', 'job__company')
    search_fields = ('student__username', 'job__title')
    raw_id_fields = ('student', 'job')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'message', 'is_read', 'created_at')
    list_filter = ('is_read',)
    search_fields = ('user__username', 'message')
