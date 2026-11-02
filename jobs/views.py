import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from users.models import StudentProfile

from .ai_analysis import analyze_resume_for_job
from .matching import match_profile_to_job, parse_skill_list
from .models import Application, Job, Notification
from .notifications import notify, notify_result, user_display_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------

def _require_recruiter(request):
    """Return None when the user is a recruiter, else a redirect response."""
    if getattr(request.user, 'user_type', '') != 'recruiter':
        messages.error(request, 'That page is only available to recruiters.')
        return redirect('dashboard')
    return None


def _require_student(request):
    if getattr(request.user, 'user_type', '') != 'student':
        messages.error(request, 'That page is only available to students.')
        return redirect('dashboard')
    return None


def _owned_job_or_403(request, job_id):
    """
    Fetch a job and confirm the logged-in recruiter actually posted it.

    Previously any recruiter could open /recruiter/jobs/<id>/ for any other
    recruiter's job and read their applicant list.
    """
    job = get_object_or_404(Job, id=job_id)
    if job.posted_by_id != request.user.id and not request.user.is_superuser:
        return None, HttpResponseForbidden(
            'You do not have permission to manage this job.')
    return job, None


def _owned_application_or_403(request, app_id):
    application = get_object_or_404(
        Application.objects.select_related('job', 'student'), id=app_id)
    if application.job.posted_by_id != request.user.id \
            and not request.user.is_superuser:
        return None, HttpResponseForbidden(
            'You do not have permission to manage this applicant.')
    return application, None


# ---------------------------------------------------------------------------
# Recruiter: post a job
# ---------------------------------------------------------------------------

@login_required
def post_job(request):

    denied = _require_recruiter(request)
    if denied:
        return denied

    if request.method == 'POST':

        title = (request.POST.get('title') or '').strip()
        company = (request.POST.get('company') or '').strip()
        description = (request.POST.get('description') or '').strip()
        required_skills = (request.POST.get('required_skills') or '').strip()
        role = (request.POST.get('role') or 'Python').strip()

        try:
            min_score_required = int(request.POST.get('min_score_required', 3))
        except (TypeError, ValueError):
            min_score_required = 3
        min_score_required = max(0, min(50, min_score_required))

        valid_roles = [value for value, _ in Job.ROLE_CHOICES]
        if role not in valid_roles:
            role = 'Python'

        errors = []
        if not title:
            errors.append('Job title is required.')
        if not company:
            errors.append('Company name is required.')
        if not description:
            errors.append('Job description is required.')
        if not required_skills:
            errors.append(
                'Required skills are needed for the AI match to work.')

        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, 'jobs/post_job.html', {
                'role_choices': Job.ROLE_CHOICES,
                'submitted': request.POST,
            })

        # Store skills back in a clean canonical form so matching is stable.
        normalised = ', '.join(parse_skill_list(required_skills))

        job = Job.objects.create(
            title=title,
            company=company,
            description=description,
            required_skills=normalised or required_skills,
            role=role,
            min_score_required=min_score_required,
            posted_by=request.user,
        )

        messages.success(request, f'"{job.title}" has been posted.')
        return redirect('recruiter_jobs')

    return render(request, 'jobs/post_job.html', {
        'role_choices': Job.ROLE_CHOICES,
    })


# ---------------------------------------------------------------------------
# Student: browse / search jobs
# ---------------------------------------------------------------------------

@login_required
def job_list(request):
    """
    Job board with server-side search and filtering.

    Query parameters:
        q      - free text across title, company, description, skills
        company- exact-ish company filter
        role    - one of Job.ROLE_CHOICES
        skill   - a single required skill
        sort    - 'match' (default) or 'recent'
    """
    query = (request.GET.get('q') or '').strip()
    company = (request.GET.get('company') or '').strip()
    role = (request.GET.get('role') or '').strip()
    skill = (request.GET.get('skill') or '').strip()
    sort = (request.GET.get('sort') or 'match').strip()

    jobs = Job.objects.select_related('posted_by').all()

    if query:
        jobs = jobs.filter(
            Q(title__icontains=query)
            | Q(company__icontains=query)
            | Q(description__icontains=query)
            | Q(required_skills__icontains=query)
            | Q(role__icontains=query)
        )

    if company:
        jobs = jobs.filter(company__icontains=company)

    if role:
        jobs = jobs.filter(role__iexact=role)

    if skill:
        # Match the raw text and each canonical alias of the typed skill so
        # "reactjs" still finds a job that lists "React".
        skill_filter = Q(required_skills__icontains=skill)
        for canonical in parse_skill_list(skill):
            skill_filter |= Q(required_skills__icontains=canonical)
        jobs = jobs.filter(skill_filter)

    jobs = list(jobs.distinct())

    # Attach this student's application status + live match percentage.
    profile = None
    if request.user.user_type == 'student':
        profile, _ = StudentProfile.objects.get_or_create(user=request.user)

    applications = {
        app.job_id: app
        for app in Application.objects.filter(student=request.user)
    }

    for job in jobs:
        application = applications.get(job.id)
        job.application = application
        job.status = application.status if application else None

        if profile is not None:
            result = match_profile_to_job(profile, job)
            job.match_percentage = result['percentage']
            job.matched_skills = result['matched']
            job.missing_skills = result['missing']
            job.has_requirements = result['has_requirements']
        else:
            job.match_percentage = 0
            job.matched_skills = []
            job.missing_skills = []
            job.has_requirements = bool(job.required_skills)

    if sort == 'match' and profile is not None:
        jobs.sort(key=lambda j: j.match_percentage, reverse=True)

    companies = Job.objects.values_list('company', flat=True).distinct()

    return render(request, 'jobs/job_list.html', {
        'jobs': jobs,
        'profile': profile,
        'q': query,
        'company': company,
        'role': role,
        'skill': skill,
        'sort': sort,
        'role_choices': Job.ROLE_CHOICES,
        'companies': sorted({c for c in companies if c}),
        'has_filters': any([query, company, role, skill]),
    })


# ---------------------------------------------------------------------------
# Student: job detail + AI analysis
# ---------------------------------------------------------------------------

@login_required
def job_detail(request, job_id):
    """
    Full job view with the local resume/job/quiz match analysis.

    The analysis is generated on demand and cached; nothing is written to
    the database, so no schema change was needed for it.
    """
    job = get_object_or_404(Job, id=job_id)

    profile = None
    analysis = None
    application = None

    if request.user.user_type == 'student':
        profile, _ = StudentProfile.objects.get_or_create(user=request.user)
        application = Application.objects.filter(
            student=request.user, job=job).first()

        refresh = request.GET.get('refresh') == '1'
        analysis = analyze_resume_for_job(profile, job, use_cache=not refresh)

    return render(request, 'jobs/job_detail.html', {
        'job': job,
        'profile': profile,
        'application': application,
        'analysis': analysis,
    })


# ---------------------------------------------------------------------------
# Student: quiz entry point + apply
# ---------------------------------------------------------------------------

@login_required
def start_quiz(request, job_id):

    denied = _require_student(request)
    if denied:
        return denied

    job = get_object_or_404(Job, id=job_id)

    from quiz.models import QuizAttempt

    if QuizAttempt.objects.filter(user=request.user, job=job).exists():
        messages.info(
            request,
            'You have already attempted the quiz for this job. '
            'Only one attempt per job is allowed.')
        return redirect('job_list')

    # Make the candidate visible to the recruiter (with their resume match)
    # as soon as they show interest, rather than only once they finish the
    # quiz. get_or_create is idempotent, so this never overwrites a status
    # that already exists (e.g. a retry after the row was already created).
    Application.objects.get_or_create(
        student=request.user, job=job,
        defaults={'status': Application.STATUS_QUIZ_PENDING},
    )

    # Bind the quiz session to this job. Storing the job id here (rather than
    # leaving a stale one behind) is what stops questions from a previous
    # job leaking into this attempt.
    request.session['job_id'] = job.id
    request.session.pop('quiz_questions', None)
    request.session.pop('current_index', None)
    request.session.pop('answers', None)
    request.session.modified = True

    return redirect('quiz')


@login_required
@require_POST
def apply_job(request, job_id):
    """
    Confirm an application after the student has passed the quiz.

    POST-only so the action cannot be triggered by a stray link, image tag
    or prefetch.
    """
    denied = _require_student(request)
    if denied:
        return denied

    job = get_object_or_404(Job, id=job_id)

    application = Application.objects.filter(
        student=request.user, job=job).first()

    if application is None:
        messages.error(request,
                       'Take the eligibility quiz before applying.')
        return redirect('job_list')

    if application.status == Application.STATUS_APPLIED:
        messages.info(request, 'You have already applied to this job.')
        return redirect('job_list')

    if application.status != Application.STATUS_ELIGIBLE:
        messages.error(
            request,
            'You are not currently eligible to apply for this job.')
        return redirect('job_list')

    application.status = Application.STATUS_APPLIED
    application.save(update_fields=['status', 'status_updated_at'])

    notify(
        request.user,
        f'Your application for {job.title} at {job.company} was submitted.',
        email_subject='Application submitted - Naukari Adda',
        email_body=(
            'Hello {name},\n\n'
            f'Your application for {job.title} at {job.company} has been '
            f'submitted successfully.\n\n'
            f'You can track its status from your dashboard.'
        ),
    )

    # Let the recruiter know too (in-app only, no email spam).
    Notification.objects.create(
        user=job.posted_by,
        message=f'{request.user.username} applied for {job.title}.',
    )

    messages.success(request, f'Applied to {job.title}.')
    return redirect('job_list')


# ---------------------------------------------------------------------------
# Recruiter: own jobs
# ---------------------------------------------------------------------------

@login_required
def recruiter_jobs(request):

    denied = _require_recruiter(request)
    if denied:
        return denied

    jobs = list(Job.objects.filter(posted_by=request.user))

    counts = {}
    for application in Application.objects.filter(job__in=jobs):
        counts[application.job_id] = counts.get(application.job_id, 0) + 1

    for job in jobs:
        job.applicant_count = counts.get(job.id, 0)

    return render(request, 'jobs/recruiter_jobs.html', {'jobs': jobs})


# ---------------------------------------------------------------------------
# Recruiter: applicants + AI matching
# ---------------------------------------------------------------------------

@login_required
def view_applicants(request, job_id):

    denied = _require_recruiter(request)
    if denied:
        return denied

    job, forbidden = _owned_job_or_403(request, job_id)
    if forbidden:
        return forbidden

    from quiz.models import QuizAttempt

    applications = list(
        Application.objects.filter(job=job).select_related('student'))

    profiles = {
        profile.user_id: profile
        for profile in StudentProfile.objects.filter(
            user__in=[app.student_id for app in applications])
    }

    attempts = {}
    for attempt in QuizAttempt.objects.filter(
            job=job, user__in=[app.student_id for app in applications]
    ).order_by('attempted_at'):
        attempts[attempt.user_id] = attempt

    for application in applications:

        attempt = attempts.get(application.student_id)

        # `quiz_attempted` distinguishes "scored 0" from "never took it" -
        # the two used to be conflated (score defaulted to 0 either way),
        # which is exactly why the applicant page could look like a
        # candidate had failed the quiz when they simply hadn't reached it
        # yet. quiz_score/quiz_total stay 0 when unattempted purely so any
        # existing code comparing them numerically (e.g. sorting) keeps
        # working; the template shows "Not Attempted" using the new flag
        # instead of ever printing a fabricated "0".
        application.quiz_attempted = attempt is not None
        application.quiz_score = attempt.score if attempt else 0
        application.quiz_total = attempt.total if attempt else 0

        profile = profiles.get(application.student_id)
        application.profile = profile

        # Resume/skill match. This has always been computed independently
        # of quiz status, so it already works without a quiz attempt - the
        # only real fix needed elsewhere is not to conflate it with the
        # quiz score below.
        result = match_profile_to_job(profile, job)

        application.match_percentage = result['percentage']
        application.matched_skills = result['matched']
        application.missing_skills = result['missing']
        application.match_skills = ', '.join(result['matched'])
        application.has_requirements = result['has_requirements']
        application.has_resume = bool(
            profile and (profile.resume_text or '').strip())

        # Overall match: resume/skill match (job compatibility) weighted
        # with quiz performance when a quiz has actually been taken. When
        # it hasn't, the overall score is the resume match alone - never a
        # fabricated quiz component and never forced to 0 for that reason.
        if application.quiz_attempted and application.quiz_total:
            application.quiz_percentage = int(round(
                application.quiz_score / application.quiz_total * 100))
            application.overall_match_percentage = int(round(
                (0.6 * result['percentage']) +
                (0.4 * application.quiz_percentage)
            ))
        else:
            application.quiz_percentage = None
            application.overall_match_percentage = result['percentage']

        application.recommended = (
            result['percentage'] >= 70
            and application.quiz_attempted
            and application.quiz_score >= job.min_score_required
        )

    applications.sort(
        key=lambda a: (a.match_percentage, a.quiz_score),
        reverse=True,
    )

    return render(request, 'jobs/view_applicants.html', {
        'job': job,
        'applications': applications,
        'status_choices': Application.RECRUITER_SETTABLE,
    })


@login_required
def applicant_analysis(request, app_id):
    """Recruiter-facing local match analysis for a single applicant."""

    denied = _require_recruiter(request)
    if denied:
        return denied

    application, forbidden = _owned_application_or_403(request, app_id)
    if forbidden:
        return forbidden

    profile = StudentProfile.objects.filter(
        user=application.student).first()

    refresh = request.GET.get('refresh') == '1'
    analysis = analyze_resume_for_job(
        profile, application.job, use_cache=not refresh)

    return render(request, 'jobs/applicant_analysis.html', {
        'application': application,
        'job': application.job,
        'profile': profile,
        'analysis': analysis,
    })


@login_required
@require_POST
def email_selected_applicants(request, job_id):
    """Send individualized bulk emails to applicants of the recruiter's job."""
    denied = _require_recruiter(request)
    if denied:
        return denied

    job, forbidden = _owned_job_or_403(request, job_id)
    if forbidden:
        return forbidden

    # Preserve submitted order while removing duplicate IDs.
    application_ids = list(dict.fromkeys(
        (value or '').strip()
        for value in request.POST.getlist('application_ids')
        if (value or '').strip()
    ))
    subject = (request.POST.get('subject') or '').replace(
        '\\r', ' ').replace('\\n', ' ').strip()[:200]
    body = (request.POST.get('body') or '').strip()

    if not application_ids:
        messages.error(request, 'Select at least one candidate to email.')
        return redirect('view_applicants', job_id=job.id)
    if not subject or not body:
        messages.error(request, 'Enter both a subject and a message.')
        return redirect('view_applicants', job_id=job.id)
    if len(body) > 5000:
        messages.error(request, 'Message must be 5,000 characters or fewer.')
        return redirect('view_applicants', job_id=job.id)

    applications = list(
        Application.objects.filter(
            id__in=application_ids, job=job
        ).select_related('student')
    )
    # Keep the submitted ordering for clear delivery feedback.
    by_id = {str(app.id): app for app in applications}
    applications = [by_id[value] for value in application_ids
                    if value in by_id]

    sent_names, missing_names, failed_names = [], [], []
    for application in applications:
        name = user_display_name(application.student)
        result = notify_result(
            application.student,
            f'You have a new message from {job.company} regarding {job.title}.',
            email_subject=subject,
            email_body=body,
        )
        if result == 'sent':
            sent_names.append(name)
        elif result == 'no_email':
            missing_names.append(name)
        else:
            failed_names.append(name)

    # IDs outside this job are intentionally ignored; ownership is enforced
    # by the job-scoped query above.
    if sent_names:
        messages.success(
            request, f'Email sent to {len(sent_names)} candidate(s): '
            + ', '.join(sent_names))
    if missing_names:
        messages.warning(
            request, 'No valid email address on file for: '
            + ', '.join(missing_names))
    if failed_names:
        messages.error(
            request, 'Email delivery failed for: ' + ', '.join(failed_names))
    if not (sent_names or missing_names or failed_names):
        messages.warning(request, 'No matching applicants were selected.')

    return redirect('view_applicants', job_id=job.id)


# ---------------------------------------------------------------------------
# Recruiter: status changes
# ---------------------------------------------------------------------------

def _apply_status(request, application, new_status, email_subject, email_body,
                  in_app_message):
    """
    Atomically change status only when it differs from the current status.
    A repeated click therefore cannot create a duplicate notification/email.
    """
    with transaction.atomic():
        changed = Application.objects.filter(
            pk=application.pk
        ).exclude(status=new_status).update(
            status=new_status,
            status_updated_at=timezone.now(),
        )

    application.refresh_from_db()
    if not changed:
        messages.info(
            request,
            f'{user_display_name(application.student)} is already marked '
            f'as {new_status}; no duplicate email was sent.')
        return False

    result = notify_result(
        application.student,
        in_app_message,
        email_subject=email_subject,
        email_body=email_body,
    )

    name = user_display_name(application.student)
    if result == 'sent':
        messages.success(
            request, f'{name} marked as {new_status}. Notification and email sent.')
    elif result == 'no_email':
        messages.warning(
            request, f'{name} marked as {new_status}. In-app notification '
            'created, but the student has no valid email address.')
    else:
        messages.warning(
            request, f'{name} marked as {new_status}. In-app notification '
            'created, but email delivery failed.')
    return True


@login_required
@require_POST
def update_application_status(request, app_id):
    """Generic status update used by the applicant list dropdown."""

    denied = _require_recruiter(request)
    if denied:
        return denied

    application, forbidden = _owned_application_or_403(request, app_id)
    if forbidden:
        return forbidden

    new_status = (request.POST.get('status') or '').strip()

    if new_status not in Application.RECRUITER_SETTABLE:
        messages.error(request, 'That is not a valid status.')
        return redirect('view_applicants', job_id=application.job_id)

    if new_status == Application.STATUS_INTERVIEW:
        # Interviews need date/time/link, so send the recruiter to the form.
        return redirect('schedule_interview', app_id=application.id)

    job = application.job

    bodies = {
        Application.STATUS_SHORTLISTED: (
            'You have been shortlisted - Naukari Adda',
            'Hello {name},\n\n'
            f'You have been shortlisted for {job.title} at {job.company}.\n'
            f'The recruiter will be in touch with next steps.',
            f'You were shortlisted for {job.title} at {job.company}.',
        ),
        Application.STATUS_SELECTED: (
            'Congratulations - you have been selected',
            'Hello {name},\n\n'
            f'Congratulations! You have been selected for {job.title} at '
            f'{job.company}.',
            f'You were selected for {job.title} at {job.company}.',
        ),
        Application.STATUS_REJECTED: (
            'Application update - Naukari Adda',
            'Hello {name},\n\n'
            f'Thank you for applying for {job.title} at {job.company}. '
            f'On this occasion you have not been selected.\n\n'
            f'We wish you the best for your search.',
            f'Your application for {job.title} was not successful.',
        ),
    }

    subject, body, in_app = bodies[new_status]
    _apply_status(request, application, new_status, subject, body, in_app)

    return redirect('view_applicants', job_id=application.job_id)


@login_required
@require_POST
def select_applicant(request, app_id):

    denied = _require_recruiter(request)
    if denied:
        return denied

    application, forbidden = _owned_application_or_403(request, app_id)
    if forbidden:
        return forbidden

    job = application.job

    _apply_status(
        request,
        application,
        Application.STATUS_SELECTED,
        'Congratulations - you have been selected',
        'Hello {name},\n\n'
        f'Congratulations! You have been selected for {job.title} at '
        f'{job.company}.',
        f'You were selected for {job.title} at {job.company}.',
    )

    return redirect('view_applicants', job_id=application.job_id)


@login_required
@require_POST
def reject_applicant(request, app_id):

    denied = _require_recruiter(request)
    if denied:
        return denied

    application, forbidden = _owned_application_or_403(request, app_id)
    if forbidden:
        return forbidden

    job = application.job

    _apply_status(
        request,
        application,
        Application.STATUS_REJECTED,
        'Application update - Naukari Adda',
        'Hello {name},\n\n'
        f'Thank you for applying for {job.title} at {job.company}. '
        f'On this occasion you have not been selected.',
        f'Your application for {job.title} was not successful.',
    )

    return redirect('view_applicants', job_id=application.job_id)


@login_required
def schedule_interview(request, app_id):

    denied = _require_recruiter(request)
    if denied:
        return denied

    application, forbidden = _owned_application_or_403(request, app_id)
    if forbidden:
        return forbidden

    if request.method == 'POST':

        date = (request.POST.get('date') or '').strip()
        time = (request.POST.get('time') or '').strip()
        meeting_link = (request.POST.get('meeting_link') or '').strip()

        if not date or not time:
            messages.error(request, 'Interview date and time are required.')
            return render(request, 'jobs/schedule_interview.html',
                          {'application': application})

        application.interview_date = date
        application.interview_time = time
        application.meeting_link = meeting_link or None
        application.status = Application.STATUS_INTERVIEW

        try:
            application.full_clean(exclude=['student', 'job'])
        except Exception as exc:                  # noqa: BLE001
            messages.error(request, f'Could not save the interview: {exc}')
            return render(request, 'jobs/schedule_interview.html',
                          {'application': application})

        application.save()

        job = application.job

        emailed = notify(
            application.student,
            f'Interview scheduled for {job.title} on '
            f'{application.interview_date} at {application.interview_time}.',
            email_subject='Interview scheduled - Naukari Adda',
            email_body=(
                'Hello {name},\n\n'
                f'Your interview for {job.title} at {job.company} has been '
                f'scheduled.\n\n'
                f'Date: {application.interview_date}\n'
                f'Time: {application.interview_time}\n'
                f'Meeting link: {application.meeting_link or "To be shared"}\n\n'
                f'Good luck!'
            ),
        )

        if emailed:
            messages.success(request, 'Interview scheduled and email sent.')
        else:
            messages.warning(
                request,
                'Interview scheduled. The student was notified in-app, but '
                'the email could not be sent.')

        return redirect('view_applicants', job_id=application.job_id)

    return render(request, 'jobs/schedule_interview.html', {
        'application': application,
    })


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@login_required
@require_POST
def mark_notifications_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(
        is_read=True)
    return redirect('dashboard')
