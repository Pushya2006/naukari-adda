import logging
import random

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from jobs.models import Application, Job
from jobs.notifications import notify

from .models import Question, QuizAnswer, QuizAttempt

logger = logging.getLogger(__name__)

QUESTIONS_PER_QUIZ = 5
SECONDS_PER_QUESTION = 30

# --- Proctoring limits (whole-quiz totals, not per-question) ---------------
# Face and voice each terminate ON the 3rd warning (i.e. 3 warnings allowed
# total, the 3rd one ends the quiz). Tab switching is more lenient: 3
# switches are allowed without penalty and only the 4th one terminates -
# this asymmetry is intentional, matching how each signal behaves in
# practice (a momentary tab switch is far less disruptive than repeated
# camera/mic violations).
FACE_WARNING_LIMIT = 3
VOICE_WARNING_LIMIT = 3
TAB_SWITCH_LIMIT = 3

# Session keys used to persist warning counts across the many separate page
# loads that make up one quiz attempt (one full page render per question).
VIOLATION_SESSION_KEYS = {
    'face': 'quiz_face_warnings',
    'voice': 'quiz_voice_warnings',
    'tab': 'quiz_tab_switches',
}


def _clear_quiz_session(session):
    for key in ('job_id', 'quiz_questions', 'current_index', 'answers',
                'quiz_face_warnings', 'quiz_voice_warnings',
                'quiz_tab_switches'):
        session.pop(key, None)
    session.modified = True


@login_required
def quiz_view(request):

    if request.user.user_type != 'student':
        messages.error(request, 'Only students can take the eligibility quiz.')
        return redirect('dashboard')

    # The quiz can only be reached through "Take Quiz" on a job.
    job_id = request.session.get('job_id')
    if not job_id:
        messages.info(request, 'Choose a job and start its quiz first.')
        return redirect('job_list')

    job = get_object_or_404(Job, id=job_id)

    # --- One attempt per job ------------------------------------------------
    existing = QuizAttempt.objects.filter(user=request.user, job=job).first()
    if existing:
        _clear_quiz_session(request.session)
        return render(request, 'quiz/result.html', {
            'already_attempted': True,
            'score': existing.score,
            'total': existing.total,
            'job': job,
            'status': Application.objects.filter(
                student=request.user, job=job
            ).values_list('status', flat=True).first(),
        })

    # --- Build the question set --------------------------------------------
    # The stored question set is bound to a job id. Without this check a
    # student who abandoned job A's quiz would be served A's questions when
    # starting job B's quiz.
    session_job = request.session.get('quiz_job_id')

    if 'quiz_questions' not in request.session or session_job != job.id:

        question_ids = list(
            Question.objects.filter(role=job.role).values_list('id', flat=True))

        random.shuffle(question_ids)
        question_ids = question_ids[:QUESTIONS_PER_QUIZ]

        if not question_ids:
            _clear_quiz_session(request.session)
            request.session.pop('quiz_job_id', None)
            messages.warning(
                request,
                f'No quiz questions have been added for the "{job.role}" '
                f'role yet. Please contact the administrator.')
            return render(request, 'quiz/result.html', {
                'no_questions': True,
                'job': job,
            })

        request.session['quiz_questions'] = question_ids
        request.session['quiz_job_id'] = job.id
        request.session['current_index'] = 0
        request.session['answers'] = {}
        # Fresh attempt - proctoring counters always start at 0.
        request.session['quiz_face_warnings'] = 0
        request.session['quiz_voice_warnings'] = 0
        request.session['quiz_tab_switches'] = 0
        request.session.modified = True

    ordered_ids = request.session['quiz_questions']

    # Preserve the shuffled order (a plain id__in filter returns DB order).
    by_id = {q.id: q for q in Question.objects.filter(id__in=ordered_ids)}
    questions = [by_id[qid] for qid in ordered_ids if qid in by_id]

    if not questions:
        _clear_quiz_session(request.session)
        request.session.pop('quiz_job_id', None)
        messages.error(request, 'The quiz could not be loaded. Please retry.')
        return redirect('job_list')

    current_index = request.session.get('current_index', 0)

    # --- Handle a submitted answer -----------------------------------------
    if request.method == 'POST':

        selected = request.POST.get('answer')
        question_id = request.POST.get('question_id')

        answers = request.session.get('answers', {})

        # Only accept an answer for the question we actually served, and only
        # a value in range. Stops a crafted POST from stuffing the session.
        if selected and question_id:
            try:
                selected_int = int(selected)
                question_id_int = int(question_id)
            except (TypeError, ValueError):
                selected_int = question_id_int = None

            if (selected_int in (1, 2, 3, 4)
                    and question_id_int in ordered_ids):
                answers[str(question_id_int)] = selected_int

        request.session['answers'] = answers
        request.session['current_index'] = current_index + 1
        request.session.modified = True

        current_index = request.session['current_index']

        # --- Finished ------------------------------------------------------
        if current_index >= len(questions):
            return _finish_quiz(request, job, questions, answers)

    if current_index >= len(questions):
        # Defensive: stale index from an old session.
        request.session['current_index'] = 0
        current_index = 0

    return render(request, 'quiz/quiz.html', {
        'question': questions[current_index],
        'current': current_index + 1,
        'total': len(questions),
        'job': job,
        'seconds': SECONDS_PER_QUESTION,
        'tab_switch_count': request.session.get('quiz_tab_switches', 0),
        'face_warning_count': request.session.get('quiz_face_warnings', 0),
        'voice_warning_count': request.session.get('quiz_voice_warnings', 0),
        'tab_switch_limit': TAB_SWITCH_LIMIT,
        'face_warning_limit': FACE_WARNING_LIMIT,
        'voice_warning_limit': VOICE_WARNING_LIMIT,
    })


def _score_and_finalize(request, job, questions, answers,
                        termination_reason=None):
    """
    Score whatever has been answered, persist the QuizAttempt/Application,
    and notify the student. Shared by a normal finish (_finish_quiz) and a
    proctoring-triggered termination (record_violation), so both paths use
    the exact same scoring/eligibility logic.
    """
    score = 0

    for question in questions:

        selected = answers.get(str(question.id))
        is_correct = selected is not None and int(selected) == \
            question.correct_option

        if is_correct:
            score += 1

        if selected is not None:
            QuizAnswer.objects.update_or_create(
                user=request.user,
                question=question,
                defaults={
                    'selected_option': int(selected),
                    'is_correct': is_correct,
                },
            )

    if score >= job.min_score_required:
        status = Application.STATUS_ELIGIBLE
    else:
        status = Application.STATUS_NOT_ELIGIBLE

    QuizAttempt.objects.create(
        user=request.user,
        job=job,
        score=score,
        total=len(questions),
    )

    application, _ = Application.objects.update_or_create(
        student=request.user,
        job=job,
        defaults={'status': status},
    )

    if termination_reason:
        message = (
            f'Your quiz for {job.title} was automatically submitted after '
            f'{termination_reason}. You scored {score}/{len(questions)} - '
            f'{status}.'
        )
    else:
        message = (
            f'You scored {score}/{len(questions)} on the {job.title} quiz - '
            f'{status}.'
        )

    notify(request.user, message)

    _clear_quiz_session(request.session)
    request.session.pop('quiz_job_id', None)

    return score, status, application


def _finish_quiz(request, job, questions, answers):
    """Normal completion path: last question answered/timer expired."""

    # Guard against a double submit creating two attempts.
    if QuizAttempt.objects.filter(user=request.user, job=job).exists():
        _clear_quiz_session(request.session)
        request.session.pop('quiz_job_id', None)
        return redirect('job_list')

    score, status, application = _score_and_finalize(
        request, job, questions, answers)

    return render(request, 'quiz/result.html', {
        'score': score,
        'total': len(questions),
        'status': status,
        'job': job,
        'application': application,
        'min_score_required': job.min_score_required,
    })


# ---------------------------------------------------------------------------
# Proctoring violations (face / voice / tab-switch)
#
# The quiz re-renders a full page per question, so client-side JS state
# (counters) cannot survive a page load - the counts live in the session
# instead, exactly like `current_index` and `answers` already do. The
# browser reports a violation the moment it happens via a small fetch()
# call; this view increments the matching session counter and, once a
# limit is reached, immediately finalizes the attempt using the same
# scoring logic as a normal finish (whatever was answered so far still
# counts), then tells the browser where to go so the "already attempted"
# result page takes over from there.
# ---------------------------------------------------------------------------

_TERMINATION_REASONS = {
    'face': 'repeated face-detection warnings',
    'voice': 'repeated voice-activity warnings',
    'tab': 'repeated tab switching',
}


@login_required
@require_POST
def record_violation(request):

    if request.user.user_type != 'student':
        return JsonResponse({'error': 'forbidden'}, status=403)

    kind = request.POST.get('type')
    if kind not in VIOLATION_SESSION_KEYS:
        return JsonResponse({'error': 'invalid violation type'}, status=400)

    job_id = request.session.get('job_id')
    if not job_id or 'quiz_questions' not in request.session:
        # No quiz currently in progress for this session - nothing to do.
        return JsonResponse({'error': 'no active quiz'}, status=409)

    session_key = VIOLATION_SESSION_KEYS[kind]
    count = request.session.get(session_key, 0) + 1
    request.session[session_key] = count
    request.session.modified = True

    if kind == 'tab':
        limit = TAB_SWITCH_LIMIT
        terminate = count > limit               # 4th switch terminates
    elif kind == 'face':
        limit = FACE_WARNING_LIMIT
        terminate = count >= limit               # 3rd warning terminates
    else:
        limit = VOICE_WARNING_LIMIT
        terminate = count >= limit               # 3rd warning terminates

    response = {
        'type': kind,
        'count': count,
        'limit': limit,
        'terminated': False,
    }

    if terminate:
        job = get_object_or_404(Job, id=job_id)

        # Only finalize if this attempt hasn't already been finished by a
        # near-simultaneous request (e.g. two violation reports landing at
        # once, or the timer expiring at the same moment).
        if not QuizAttempt.objects.filter(
                user=request.user, job=job).exists():

            ordered_ids = request.session.get('quiz_questions', [])
            by_id = {q.id: q for q in
                     Question.objects.filter(id__in=ordered_ids)}
            questions = [by_id[qid] for qid in ordered_ids if qid in by_id]
            answers = request.session.get('answers', {})

            _score_and_finalize(
                request, job, questions, answers,
                termination_reason=_TERMINATION_REASONS[kind])
        else:
            _clear_quiz_session(request.session)
            request.session.pop('quiz_job_id', None)

        response['terminated'] = True
        response['redirect_url'] = reverse('quiz')

    return JsonResponse(response)
