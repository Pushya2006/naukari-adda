import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from jobs.matching import extract_skills_from_text, parse_skill_list
from jobs.models import Application, Job, Notification

from .forms import ProfileForm, RegisterForm, ResumeForm
from .models import StudentProfile
from .resume_utils import extract_resume_text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resume upload + extraction
# ---------------------------------------------------------------------------

@login_required
def upload_resume(request):

    if request.user.user_type != 'student':
        messages.error(request, 'Only students can upload a resume.')
        return redirect('dashboard')

    profile, _ = StudentProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':

        form = ResumeForm(request.POST, request.FILES, instance=profile)

        if form.is_valid():

            profile = form.save()

            # --- Extract text (pdfplumber -> PyPDF2 -> OCR) ---------------
            text = ''
            try:
                text = extract_resume_text(profile.resume.path)
            except Exception as exc:              # noqa: BLE001
                logger.error('Resume extraction failed for %s: %s',
                             request.user.username, exc)

            profile.resume_text = text

            # --- Detect skills and actually persist them ------------------
            # (The previous code assigned profile.skills to a field that did
            #  not exist, so nothing was ever saved and every match was 0%.)
            detected = extract_skills_from_text(text)

            # Keep any skills the student typed in manually.
            existing = parse_skill_list(profile.skills or '')
            merged = existing + [s for s in detected if s not in existing]

            profile.skills = ', '.join(merged)
            profile.save()

            if not text.strip():
                messages.warning(
                    request,
                    'Your resume was uploaded, but no text could be read '
                    'from it. If it is a scanned document, install Tesseract '
                    'OCR, or add your skills manually on your profile page.')
            elif detected:
                messages.success(
                    request,
                    'Resume uploaded. Detected skills: '
                    + ', '.join(detected[:12])
                    + ('...' if len(detected) > 12 else ''))
            else:
                messages.info(
                    request,
                    'Resume uploaded, but no known skills were recognised. '
                    'You can add them manually on your profile page.')

            return redirect('profile')

        messages.error(request, 'Please correct the errors below.')

    else:
        form = ResumeForm(instance=profile)

    return render(request, 'users/upload_resume.html', {
        'form': form,
        'profile': profile,
    })


# ---------------------------------------------------------------------------
# Candidate profile
# ---------------------------------------------------------------------------

@login_required
def profile_view(request):

    if request.user.user_type != 'student':
        messages.error(request, 'Only students have a candidate profile.')
        return redirect('dashboard')

    profile, _ = StudentProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':

        form = ProfileForm(request.POST, instance=profile)

        if form.is_valid():
            updated = form.save(commit=False)
            # Store skills canonically so matching stays consistent.
            updated.skills = ', '.join(parse_skill_list(updated.skills or ''))
            updated.save()

            email = (form.cleaned_data.get('email') or '').strip()
            if email != (request.user.email or ''):
                request.user.email = email
                request.user.save(update_fields=['email'])

            messages.success(request, 'Profile updated.')
            return redirect('profile')

        messages.error(request, 'Please correct the errors below.')

    else:
        form = ProfileForm(instance=profile,
                           initial={'email': request.user.email})

    return render(request, 'users/profile.html', {
        'form': form,
        'profile': profile,
        'completion': profile.completion_percentage,
        'breakdown': profile.completion_breakdown(),
        'missing': profile.missing_profile_items,
        'skills': profile.skill_list,
    })


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def register_view(request):

    if request.user.is_authenticated:
        # A signed-in visitor filling out this form clearly wants to create
        # (and switch to) a different account, not stay on their current
        # one. This used to redirect straight to the dashboard instead,
        # which silently kept the visitor on their existing session - the
        # new account was submitted but never logged into, and the person
        # landed back on their old account's dashboard instead of the one
        # they had just registered. Logging the old session out first lets
        # registration proceed normally and log into the new account.
        logout(request)

    form = RegisterForm()

    if request.method == 'POST':

        form = RegisterForm(request.POST)

        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome, {user.username}!')
            return redirect('dashboard')

        messages.error(request, 'Please correct the errors below.')

    return render(request, 'users/register.html', {'form': form})


def login_view(request):

    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':

        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''

        if not username or not password:
            messages.error(request, 'Enter both a username and a password.')
            return render(request, 'users/login.html')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            return redirect('dashboard')

        messages.error(request, 'Invalid username or password.')

    return render(request, 'users/login.html')


def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('login')


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):

    notifications = Notification.objects.filter(user=request.user)[:20]
    unread_count = Notification.objects.filter(
        user=request.user, is_read=False).count()

    # ---- Student ---------------------------------------------------------
    if request.user.user_type == 'student':

        profile, _ = StudentProfile.objects.get_or_create(user=request.user)

        applications = Application.objects.filter(
            student=request.user).select_related('job')

        status_counts = {}
        for application in applications:
            status_counts[application.status] = \
                status_counts.get(application.status, 0) + 1

        return render(request, 'student_dashboard.html', {
            'profile': profile,
            'applications': applications,
            'notifications': notifications,
            'unread_count': unread_count,
            'completion': profile.completion_percentage,
            'missing': profile.missing_profile_items,
            'skills': profile.skill_list,
            'status_counts': status_counts,
            'total_applications': applications.count(),
        })

    # ---- Recruiter -------------------------------------------------------
    if request.user.user_type == 'recruiter':

        jobs = Job.objects.filter(posted_by=request.user)
        applications = Application.objects.filter(
            job__posted_by=request.user)

        return render(request, 'recruiter_dashboard.html', {
            'jobs': jobs,
            'notifications': notifications,
            'unread_count': unread_count,
            'total_jobs': jobs.count(),
            'total_applications': applications.count(),
            'selected_count': applications.filter(
                status=Application.STATUS_SELECTED).count(),
            'rejected_count': applications.filter(
                status=Application.STATUS_REJECTED).count(),
            'interview_count': applications.filter(
                status=Application.STATUS_INTERVIEW).count(),
            'shortlisted_count': applications.filter(
                status=Application.STATUS_SHORTLISTED).count(),
        })

    # ---- Admin -----------------------------------------------------------
    from django.contrib.auth import get_user_model
    User = get_user_model()

    return render(request, 'admin_dashboard.html', {
        'notifications': notifications,
        'total_users': User.objects.count(),
        'total_students': User.objects.filter(user_type='student').count(),
        'total_recruiters': User.objects.filter(user_type='recruiter').count(),
        'total_jobs': Job.objects.count(),
        'total_applications': Application.objects.count(),
    })


def home(request):
    return render(request, 'home.html')
