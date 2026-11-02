"""
Regression tests for the bugs that were fixed, plus the new features.

Run with:  python manage.py test
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from users.models import StudentProfile

from .matching import (canonicalize, extract_skills_from_text, match_skills,
                       parse_skill_list)
from .models import Application, Job, Notification
from .notifications import notify

User = get_user_model()


class SkillNormalisationTests(TestCase):

    def test_case_and_space_insensitive(self):
        self.assertEqual(canonicalize('  PYTHON  '), 'Python')
        self.assertEqual(canonicalize('python3'), 'Python')
        self.assertEqual(canonicalize('My SQL'), 'MySQL')

    def test_punctuation_variants(self):
        self.assertEqual(canonicalize('React.js'), 'React')
        self.assertEqual(canonicalize('REACTJS'), 'React')
        self.assertEqual(canonicalize('node js'), 'Node.js')
        self.assertEqual(canonicalize('c++'), 'C++')
        self.assertEqual(canonicalize('power-bi'), 'Power BI')

    def test_comma_separated_with_messy_spacing(self):
        self.assertEqual(
            parse_skill_list('SQL , Python,  power bi '),
            ['SQL', 'Python', 'Power BI'],
        )

    def test_other_separators(self):
        self.assertEqual(parse_skill_list('Python; Django\nAWS|Docker'),
                         ['Python', 'Django', 'AWS', 'Docker'])

    def test_duplicates_collapse(self):
        self.assertEqual(parse_skill_list('python, Python3, PYTHON'),
                         ['Python'])

    def test_unknown_skill_is_kept(self):
        self.assertIn('Some Niche Tool',
                      parse_skill_list('Some Niche Tool, Python'))


class SkillExtractionTests(TestCase):

    def test_extracts_from_prose(self):
        text = ('Built a REST API with Django and PostgreSQL. '
                'Comfortable with Docker and CI/CD pipelines.')
        skills = extract_skills_from_text(text)
        self.assertIn('Django', skills)
        self.assertIn('PostgreSQL', skills)
        self.assertIn('Docker', skills)
        self.assertIn('REST API', skills)

    def test_java_does_not_match_javascript(self):
        skills = extract_skills_from_text('Strong in JavaScript and React.')
        self.assertIn('JavaScript', skills)
        self.assertNotIn('Java', skills)

    def test_short_skill_needs_word_boundary(self):
        # "R" should not be found inside ordinary words.
        skills = extract_skills_from_text('Responsible for marketing reports.')
        self.assertNotIn('R', skills)

    def test_empty_text_is_safe(self):
        self.assertEqual(extract_skills_from_text(''), [])
        self.assertEqual(extract_skills_from_text(None), [])


class MatchCalculationTests(TestCase):

    def test_percentage_is_computed_not_hardcoded(self):
        result = match_skills(
            candidate_skills='Python, Django',
            required_skills='Python, Django, SQL, AWS',
        )
        self.assertEqual(result['percentage'], 50)
        self.assertEqual(result['matched'], ['Python', 'Django'])
        self.assertEqual(result['missing'], ['SQL', 'AWS'])

    def test_full_match(self):
        result = match_skills('python3, DJANGO', 'Python, Django')
        self.assertEqual(result['percentage'], 100)

    def test_skill_found_only_in_resume_text(self):
        result = match_skills(
            candidate_skills='',
            required_skills='Python, Docker',
            resume_text='I used Python and Docker on my final year project.',
        )
        self.assertEqual(result['percentage'], 100)

    def test_no_requirements_does_not_crash(self):
        result = match_skills('Python', '')
        self.assertEqual(result['percentage'], 0)
        self.assertFalse(result['has_requirements'])


class ProfileMatchRegressionTests(TestCase):
    """The original 0% bug: skills were assigned to a non-existent field."""

    def setUp(self):
        self.student = User.objects.create_user(
            username='student1', password='pw12345!', user_type='student',
            email='student1@example.com')
        self.recruiter = User.objects.create_user(
            username='recruiter1', password='pw12345!', user_type='recruiter',
            email='recruiter1@example.com')
        self.job = Job.objects.create(
            title='Backend Developer', company='Acme',
            description='Build APIs', role='Python',
            required_skills='Python, Django, SQL',
            min_score_required=3, posted_by=self.recruiter)

    def test_skills_field_persists(self):
        profile = StudentProfile.objects.create(
            user=self.student, skills='Python, Django')
        profile.refresh_from_db()
        self.assertEqual(profile.skills, 'Python, Django')

    def test_match_is_not_zero_for_a_matching_candidate(self):
        from .matching import match_profile_to_job

        profile = StudentProfile.objects.create(
            user=self.student,
            skills='python, django',
            resume_text='Experienced with Python, Django and MySQL.')

        result = match_profile_to_job(profile, self.job)
        self.assertGreater(result['percentage'], 0)
        self.assertEqual(result['percentage'], 67)

    def test_missing_profile_does_not_crash(self):
        from .matching import match_profile_to_job
        result = match_profile_to_job(None, self.job)
        self.assertEqual(result['percentage'], 0)


class NotificationTests(TestCase):

    def setUp(self):
        self.student = User.objects.create_user(
            username='student2', password='pw12345!', user_type='student',
            email='student2@example.com')

    def test_notification_and_email_both_sent(self):
        sent = notify(self.student, 'Hello', 'Subject', 'Body')
        self.assertTrue(sent)
        self.assertEqual(Notification.objects.filter(
            user=self.student).count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_in_app_notification_survives_email_failure(self):
        from unittest.mock import patch

        with patch('jobs.notifications.send_mail',
                   side_effect=Exception('SMTP down')):
            sent = notify(self.student, 'Hello', 'Subject', 'Body')

        self.assertFalse(sent)
        # The important part: the in-app notification still exists.
        self.assertEqual(Notification.objects.filter(
            user=self.student).count(), 1)

    def test_missing_email_address_is_handled(self):
        no_email = User.objects.create_user(
            username='noemail', password='pw12345!', user_type='student')
        sent = notify(no_email, 'Hello', 'Subject', 'Body')
        self.assertFalse(sent)
        self.assertEqual(Notification.objects.filter(
            user=no_email).count(), 1)


class AccessControlTests(TestCase):

    def setUp(self):
        self.recruiter_a = User.objects.create_user(
            username='reca', password='pw12345!', user_type='recruiter',
            email='a@example.com')
        self.recruiter_b = User.objects.create_user(
            username='recb', password='pw12345!', user_type='recruiter',
            email='b@example.com')
        self.student = User.objects.create_user(
            username='stud', password='pw12345!', user_type='student',
            email='s@example.com')

        self.job = Job.objects.create(
            title='Job A', company='Acme', description='d', role='Python',
            required_skills='Python', min_score_required=3,
            posted_by=self.recruiter_a)

        self.application = Application.objects.create(
            student=self.student, job=self.job,
            status=Application.STATUS_APPLIED)

    def test_other_recruiter_cannot_view_applicants(self):
        self.client.login(username='recb', password='pw12345!')
        response = self.client.get(f'/recruiter/jobs/{self.job.id}/')
        self.assertEqual(response.status_code, 403)

    def test_owner_can_view_applicants(self):
        self.client.login(username='reca', password='pw12345!')
        response = self.client.get(f'/recruiter/jobs/{self.job.id}/')
        self.assertEqual(response.status_code, 200)

    def test_student_cannot_view_applicants(self):
        self.client.login(username='stud', password='pw12345!')
        response = self.client.get(f'/recruiter/jobs/{self.job.id}/')
        self.assertEqual(response.status_code, 302)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/jobs/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_other_recruiter_cannot_change_status(self):
        self.client.login(username='recb', password='pw12345!')
        response = self.client.post(
            f'/recruiter/applicant/{self.application.id}/status/',
            {'status': 'Selected'})
        self.assertEqual(response.status_code, 403)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status,
                         Application.STATUS_APPLIED)

    def test_status_update_creates_notification(self):
        self.client.login(username='reca', password='pw12345!')
        self.client.post(
            f'/recruiter/applicant/{self.application.id}/status/',
            {'status': 'Shortlisted'})
        self.application.refresh_from_db()
        self.assertEqual(self.application.status,
                         Application.STATUS_SHORTLISTED)
        self.assertTrue(Notification.objects.filter(
            user=self.student).exists())

    def test_invalid_status_is_rejected(self):
        self.client.login(username='reca', password='pw12345!')
        self.client.post(
            f'/recruiter/applicant/{self.application.id}/status/',
            {'status': 'Hired As CEO'})
        self.application.refresh_from_db()
        self.assertEqual(self.application.status,
                         Application.STATUS_APPLIED)

    def test_status_change_requires_post(self):
        self.client.login(username='reca', password='pw12345!')
        response = self.client.get(
            f'/recruiter/applicant/{self.application.id}/status/')
        self.assertEqual(response.status_code, 405)


class ApplyFlowTests(TestCase):

    def setUp(self):
        self.recruiter = User.objects.create_user(
            username='rec', password='pw12345!', user_type='recruiter',
            email='r@example.com')
        self.student = User.objects.create_user(
            username='stu', password='pw12345!', user_type='student',
            email='s@example.com')
        self.job = Job.objects.create(
            title='Dev', company='Acme', description='d', role='Python',
            required_skills='Python', min_score_required=3,
            posted_by=self.recruiter)
        self.client.login(username='stu', password='pw12345!')

    def test_cannot_apply_without_taking_quiz(self):
        self.client.post(f'/apply/{self.job.id}/')
        self.assertFalse(Application.objects.filter(
            student=self.student, job=self.job,
            status=Application.STATUS_APPLIED).exists())

    def test_cannot_apply_when_not_eligible(self):
        Application.objects.create(
            student=self.student, job=self.job,
            status=Application.STATUS_NOT_ELIGIBLE)
        self.client.post(f'/apply/{self.job.id}/')
        application = Application.objects.get(
            student=self.student, job=self.job)
        self.assertEqual(application.status,
                         Application.STATUS_NOT_ELIGIBLE)

    def test_eligible_student_can_apply(self):
        Application.objects.create(
            student=self.student, job=self.job,
            status=Application.STATUS_ELIGIBLE)
        self.client.post(f'/apply/{self.job.id}/')
        application = Application.objects.get(
            student=self.student, job=self.job)
        self.assertEqual(application.status, Application.STATUS_APPLIED)

    def test_apply_requires_post(self):
        Application.objects.create(
            student=self.student, job=self.job,
            status=Application.STATUS_ELIGIBLE)
        response = self.client.get(f'/apply/{self.job.id}/')
        self.assertEqual(response.status_code, 405)

    def test_duplicate_application_is_prevented_by_constraint(self):
        from django.db import IntegrityError, transaction

        Application.objects.create(
            student=self.student, job=self.job,
            status=Application.STATUS_APPLIED)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Application.objects.create(
                    student=self.student, job=self.job,
                    status=Application.STATUS_APPLIED)


class JobSearchTests(TestCase):

    def setUp(self):
        self.recruiter = User.objects.create_user(
            username='rec', password='pw12345!', user_type='recruiter',
            email='r@example.com')
        self.student = User.objects.create_user(
            username='stu', password='pw12345!', user_type='student',
            email='s@example.com')

        Job.objects.create(
            title='Django Developer', company='Acme', description='backend',
            role='Python', required_skills='Python, Django',
            min_score_required=3, posted_by=self.recruiter)
        Job.objects.create(
            title='Frontend Engineer', company='Globex', description='ui work',
            role='Frontend', required_skills='React, CSS',
            min_score_required=3, posted_by=self.recruiter)

        self.client.login(username='stu', password='pw12345!')

    def test_search_by_title(self):
        response = self.client.get('/jobs/', {'q': 'django'})
        self.assertContains(response, 'Django Developer')
        self.assertNotContains(response, 'Frontend Engineer')

    def test_filter_by_company(self):
        response = self.client.get('/jobs/', {'company': 'Globex'})
        self.assertContains(response, 'Frontend Engineer')
        self.assertNotContains(response, 'Django Developer')

    def test_filter_by_role(self):
        response = self.client.get('/jobs/', {'role': 'Frontend'})
        self.assertContains(response, 'Frontend Engineer')
        self.assertNotContains(response, 'Django Developer')

    def test_filter_by_skill_alias(self):
        # "reactjs" should still find the job that lists "React".
        response = self.client.get('/jobs/', {'skill': 'reactjs'})
        self.assertContains(response, 'Frontend Engineer')
        self.assertNotContains(response, 'Django Developer')

    def test_no_results_is_handled(self):
        response = self.client.get('/jobs/', {'q': 'cobol mainframe'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No jobs matched')


class AIAnalysisFallbackTests(TestCase):
    """The analyzer must be fully local - no Claude/Anthropic dependency."""

    def setUp(self):
        self.recruiter = User.objects.create_user(
            username='rec', password='pw12345!', user_type='recruiter',
            email='r@example.com')
        self.student = User.objects.create_user(
            username='stu', password='pw12345!', user_type='student',
            email='s@example.com')
        self.job = Job.objects.create(
            title='Dev', company='Acme', description='d', role='Python',
            required_skills='Python, Django, SQL', min_score_required=3,
            posted_by=self.recruiter)
        self.profile = StudentProfile.objects.create(
            user=self.student,
            skills='Python, Django',
            resume_text='Python and Django developer.')

    def test_score_is_local_and_deterministic(self):
        from .ai_analysis import analyze_resume_for_job

        result = analyze_resume_for_job(
            self.profile, self.job, use_cache=False)

        self.assertEqual(result['source'], 'local')
        self.assertEqual(result['unavailable_reason'], '')
        # Resume match: 2 of 3 required skills (Python, Django) = 67%.
        self.assertEqual(result['resume_percentage'], 67)
        self.assertIsNone(result['quiz_percentage'])
        self.assertFalse(result['quiz_attempted'])

    def test_score_unaffected_by_anthropic_api_key(self):
        """
        The score must be identical whether or not ANTHROPIC_API_KEY is
        set - it is never consulted.
        """
        import os

        from .ai_analysis import analyze_resume_for_job

        without_key = analyze_resume_for_job(
            self.profile, self.job, use_cache=False)

        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-not-a-real-key'
        try:
            with_key = analyze_resume_for_job(
                self.profile, self.job, use_cache=False)
        finally:
            os.environ.pop('ANTHROPIC_API_KEY', None)

        self.assertEqual(without_key['match_percentage'],
                         with_key['match_percentage'])
        self.assertEqual(with_key['source'], 'local')

    def test_never_imports_or_calls_anthropic(self):
        """Confirms the analyzer module has no Anthropic/Claude dependency
        at all - not merely a fallback path around one. Checks for actual
        import/usage statements, not prose mentioning why it was removed
        (the module's own docstring explains that history)."""
        import inspect

        import jobs.ai_analysis as ai_analysis_module

        source = inspect.getsource(ai_analysis_module)
        self.assertNotIn('import anthropic', source)
        self.assertNotIn('anthropic.Anthropic', source)
        self.assertNotIn('ANTHROPIC_API_KEY', source)
        self.assertNotIn('client.messages.create', source)

    def test_quiz_score_incorporated_when_available(self):
        from quiz.models import Question, QuizAttempt

        from .ai_analysis import analyze_resume_for_job

        Question.objects.create(
            role='Python', text='q', option1='a', option2='b', option3='c',
            option4='d', correct_option=1)
        QuizAttempt.objects.create(
            user=self.student, job=self.job, score=4, total=5)

        result = analyze_resume_for_job(
            self.profile, self.job, use_cache=False)

        self.assertTrue(result['quiz_attempted'])
        self.assertEqual(result['quiz_percentage'], 80)
        # Overall must differ from the no-quiz score, proving the quiz
        # factor is actually blended in, not ignored.
        self.assertNotEqual(result['match_percentage'], 67)

    def test_quiz_not_attempted_shows_not_attempted_not_zero(self):
        from .ai_analysis import analyze_resume_for_job

        result = analyze_resume_for_job(
            self.profile, self.job, use_cache=False)

        self.assertFalse(result['quiz_attempted'])
        self.assertIsNone(result['quiz_percentage'])
        self.assertIn('Not Attempted', result['explanation'])
        # Overall score must still be a real resume/job-based number, not 0.
        self.assertGreater(result['match_percentage'], 0)

    def test_personal_information_is_stripped(self):
        from .ai_analysis import _strip_personal_information

        cleaned = _strip_personal_information(
            'Contact me at ravi.kumar@example.com or +91 98765 43210')
        self.assertNotIn('ravi.kumar@example.com', cleaned)
        self.assertNotIn('98765', cleaned)

    def test_job_detail_page_renders_without_claude(self):
        self.client.login(username='stu', password='pw12345!')
        response = self.client.get(f'/jobs/{self.job.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AI Resume Analysis')

    def test_pages_never_mention_claude(self):
        """Direct proof for the 'no Claude branding anywhere' requirement."""
        self.client.login(username='stu', password='pw12345!')
        response = self.client.get(f'/jobs/{self.job.id}/')
        content = response.content.decode()
        self.assertNotIn('Claude', content)
        self.assertNotIn('claude', content.lower())



class RecruiterEmailWorkflowTests(TestCase):
    """Regression coverage for individual recruiter email delivery."""

    def setUp(self):
        self.recruiter = User.objects.create_user(
            username='emailrecruiter', password='pw12345!',
            user_type='recruiter', email='recruiter@example.com')
        self.student = User.objects.create_user(
            username='emailstudent', password='pw12345!',
            user_type='student', email='student@example.com',
            first_name='Pushya', last_name='Harini')
        self.student_two = User.objects.create_user(
            username='emailstudent2', password='pw12345!',
            user_type='student', email='student2@example.com',
            first_name='Student', last_name='Two')
        StudentProfile.objects.create(
            user=self.student, full_name='Pushya Harini')
        self.job = Job.objects.create(
            title='Python Developer', company='Acme',
            description='Build APIs', role='Python',
            required_skills='Python', min_score_required=3,
            posted_by=self.recruiter)
        self.application = Application.objects.create(
            student=self.student, job=self.job,
            status=Application.STATUS_APPLIED)
        self.application_two = Application.objects.create(
            student=self.student_two, job=self.job,
            status=Application.STATUS_APPLIED)
        self.client.login(username='emailrecruiter', password='pw12345!')

    def test_repeated_selected_status_sends_only_one_email(self):
        url = f'/recruiter/applicant/{self.application.id}/status/'
        first = self.client.post(url, {'status': Application.STATUS_SELECTED})
        second = self.client.post(url, {'status': Application.STATUS_SELECTED})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            Notification.objects.filter(user=self.student).count(), 1)
        self.assertEqual(mail.outbox[0].to, ['student@example.com'])
        self.assertIn('Hello Pushya Harini', mail.outbox[0].body)

    def test_shortlisted_and_rejected_repeats_do_not_resend(self):
        url = f'/recruiter/applicant/{self.application.id}/status/'
        for status in (Application.STATUS_SHORTLISTED,
                       Application.STATUS_SHORTLISTED,
                       Application.STATUS_REJECTED,
                       Application.STATUS_REJECTED):
            self.client.post(url, {'status': status})
        self.assertEqual(len(mail.outbox), 2)

    def test_bulk_email_sends_individual_personalized_messages(self):
        response = self.client.post(
            f'/recruiter/jobs/{self.job.id}/email-selected/',
            {
                'application_ids': [
                    str(self.application.id), str(self.application_two.id),
                    str(self.application.id),
                ],
                'subject': 'Placement update',
                'body': 'Hello {name}, this is an update.',
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, ['student@example.com'])
        self.assertEqual(mail.outbox[1].to, ['student2@example.com'])
        self.assertIn('Hello Pushya Harini', mail.outbox[0].body)
        self.assertIn('Hello Student Two', mail.outbox[1].body)

    def test_bulk_email_continues_after_one_delivery_failure(self):
        from unittest.mock import patch

        with patch('jobs.notifications.send_mail',
                   side_effect=[Exception('SMTP down'), 1]):
            response = self.client.post(
                f'/recruiter/jobs/{self.job.id}/email-selected/',
                {
                    'application_ids': [
                        str(self.application.id),
                        str(self.application_two.id),
                    ],
                    'subject': 'Placement update',
                    'body': 'Hello {name}, update.',
                })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Notification.objects.filter(
                user__in=[self.student, self.student_two]).count(), 2)

    def test_bulk_email_ignores_application_from_another_job(self):
        other_recruiter = User.objects.create_user(
            username='otherrecruiter', password='pw12345!',
            user_type='recruiter', email='other@example.com')
        other_job = Job.objects.create(
            title='Other job', company='Other Co', description='d',
            role='Python', required_skills='Python',
            min_score_required=1, posted_by=other_recruiter)
        other_app = Application.objects.create(
            student=self.student, job=other_job,
            status=Application.STATUS_APPLIED)
        self.client.post(
            f'/recruiter/jobs/{self.job.id}/email-selected/',
            {
                'application_ids': [str(other_app.id)],
                'subject': 'Test',
                'body': 'Test message',
            })
        self.assertEqual(len(mail.outbox), 0)

    def test_newly_registered_student_receives_email_at_registered_address(self):
        registration_client = self.client_class()
        response = registration_client.post('/register/', {
            'username': 'brandnewstudent',
            'email': 'brandnew@example.com',
            'user_type': 'student',
            'password1': 'StrongPassw0rd!2026',
            'password2': 'StrongPassw0rd!2026',
        })
        self.assertEqual(response.status_code, 302)
        new_student = User.objects.get(username='brandnewstudent')
        application = Application.objects.create(
            student=new_student, job=self.job,
            status=Application.STATUS_APPLIED)
        self.client.post(
            f'/recruiter/applicant/{application.id}/status/',
            {'status': Application.STATUS_SELECTED})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['brandnew@example.com'])

    def test_status_change_back_then_selected_again_sends_new_email(self):
        url = f'/recruiter/applicant/{self.application.id}/status/'
        self.client.post(url, {'status': Application.STATUS_SELECTED})
        self.client.post(url, {'status': Application.STATUS_REJECTED})
        self.client.post(url, {'status': Application.STATUS_SELECTED})
        self.assertEqual(len(mail.outbox), 3)
