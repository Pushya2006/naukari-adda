from django.urls import path
from .views import quiz_view, record_violation

urlpatterns = [
    path('quiz/', quiz_view, name='quiz'),
    path('quiz/violation/', record_violation, name='quiz_violation'),
]
