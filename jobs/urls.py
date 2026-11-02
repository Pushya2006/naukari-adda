from django.urls import path

from . import views

urlpatterns = [

    # Student
    path('jobs/', views.job_list, name='job_list'),
    path('jobs/<int:job_id>/', views.job_detail, name='job_detail'),
    path('apply/<int:job_id>/', views.apply_job, name='apply_job'),
    path('quiz/<int:job_id>/', views.start_quiz, name='start_quiz'),

    # Recruiter
    path('post-job/', views.post_job, name='post_job'),
    path('recruiter/jobs/', views.recruiter_jobs, name='recruiter_jobs'),
    path('recruiter/jobs/<int:job_id>/', views.view_applicants,
         name='view_applicants'),
    path('recruiter/applicant/<int:app_id>/analysis/',
         views.applicant_analysis, name='applicant_analysis'),
    path('recruiter/jobs/<int:job_id>/email-selected/',
         views.email_selected_applicants, name='email_selected_applicants'),
    path('recruiter/applicant/<int:app_id>/status/',
         views.update_application_status, name='update_application_status'),
    path('select/<int:app_id>/', views.select_applicant,
         name='select_applicant'),
    path('reject/<int:app_id>/', views.reject_applicant,
         name='reject_applicant'),
    path('schedule-interview/<int:app_id>/', views.schedule_interview,
         name='schedule_interview'),

    # Shared
    path('notifications/read/', views.mark_notifications_read,
         name='mark_notifications_read'),
]
