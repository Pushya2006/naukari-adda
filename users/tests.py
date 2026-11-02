from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .forms import ProfileForm, ResumeForm
from .models import StudentProfile

User = get_user_model()

MINIMAL_PDF = (
    b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
    b'2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n'
    b'trailer<</Root 1 0 R>>\n%%EOF\n'
)


class RegistrationTests(TestCase):

    def test_student_can_register(self):
        response = self.client.post('/register/', {
            'username': 'newstudent',
            'email': 'new@example.com',
            'user_type': 'student',
            'password1': 'Str0ngPassw0rd!',
            'password2': 'Str0ngPassw0rd!',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='newstudent')
        self.assertEqual(user.user_type, 'student')
        self.assertEqual(user.email, 'new@example.com')

    def test_cannot_self_register_as_admin(self):
        self.client.post('/register/', {
            'username': 'sneaky',
            'email': 'sneaky@example.com',
            'user_type': 'admin',
            'password1': 'Str0ngPassw0rd!',
            'password2': 'Str0ngPassw0rd!',
        })
        self.assertFalse(User.objects.filter(username='sneaky').exists())

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user(username='a', password='pw12345!',
                                 email='dupe@example.com',
                                 user_type='student')
        self.client.post('/register/', {
            'username': 'b',
            'email': 'dupe@example.com',
            'user_type': 'student',
            'password1': 'Str0ngPassw0rd!',
            'password2': 'Str0ngPassw0rd!',
        })
        self.assertFalse(User.objects.filter(username='b').exists())

    def test_weak_password_is_rejected(self):
        self.client.post('/register/', {
            'username': 'weak',
            'email': 'weak@example.com',
            'user_type': 'student',
            'password1': '12345',
            'password2': '12345',
        })
        self.assertFalse(User.objects.filter(username='weak').exists())


class LoginTests(TestCase):

    def setUp(self):
        User.objects.create_user(username='stu', password='pw12345!',
                                 user_type='student', email='s@example.com')

    def test_valid_login(self):
        response = self.client.post('/login/', {
            'username': 'stu', 'password': 'pw12345!'})
        self.assertEqual(response.status_code, 302)

    def test_invalid_login_shows_error(self):
        response = self.client.post('/login/', {
            'username': 'stu', 'password': 'wrong'}, follow=True)
        self.assertContains(response, 'Invalid username or password')

    def test_open_redirect_is_blocked(self):
        response = self.client.post(
            '/login/?next=https://evil.example.com',
            {'username': 'stu', 'password': 'pw12345!'})
        self.assertNotIn('evil.example.com', response['Location'])


class ResumeValidationTests(TestCase):

    def test_valid_pdf_is_accepted(self):
        upload = SimpleUploadedFile('resume.pdf', MINIMAL_PDF,
                                    content_type='application/pdf')
        form = ResumeForm(files={'resume': upload})
        self.assertTrue(form.is_valid(), form.errors)

    def test_executable_is_rejected(self):
        upload = SimpleUploadedFile('payload.exe', b'MZ\x90\x00',
                                    content_type='application/octet-stream')
        form = ResumeForm(files={'resume': upload})
        self.assertFalse(form.is_valid())

    def test_fake_pdf_extension_is_rejected(self):
        upload = SimpleUploadedFile('notreally.pdf', b'<?php echo 1; ?>',
                                    content_type='application/pdf')
        form = ResumeForm(files={'resume': upload})
        self.assertFalse(form.is_valid())

    def test_oversized_file_is_rejected(self):
        big = MINIMAL_PDF + b'0' * (6 * 1024 * 1024)
        upload = SimpleUploadedFile('big.pdf', big,
                                    content_type='application/pdf')
        form = ResumeForm(files={'resume': upload})
        self.assertFalse(form.is_valid())

    def test_empty_file_is_rejected(self):
        upload = SimpleUploadedFile('empty.pdf', b'',
                                    content_type='application/pdf')
        form = ResumeForm(files={'resume': upload})
        self.assertFalse(form.is_valid())


class ProfileTests(TestCase):

    def setUp(self):
        self.student = User.objects.create_user(
            username='stu', password='pw12345!', user_type='student',
            email='s@example.com')
        self.client.login(username='stu', password='pw12345!')

    def test_profile_page_loads(self):
        response = self.client.get('/profile/')
        self.assertEqual(response.status_code, 200)

    def test_completion_percentage_increases(self):
        profile, _ = StudentProfile.objects.get_or_create(user=self.student)
        before = profile.completion_percentage

        self.client.post('/profile/', {
            'full_name': 'Test Student',
            'email': 's@example.com',
            'phone': '9876543210',
            'headline': 'Final year student',
            'skills': 'python, django',
            'education': 'B.Tech CSE',
            'projects': 'Placement portal',
            'certifications': 'AWS Cloud Practitioner',
        })

        profile.refresh_from_db()
        self.assertGreater(profile.completion_percentage, before)

    def test_skills_are_canonicalised_on_save(self):
        self.client.post('/profile/', {
            'full_name': 'Test',
            'email': 's@example.com',
            'skills': 'python3 ,  REACT.JS',
            'phone': '', 'headline': '', 'education': '',
            'projects': '', 'certifications': '',
        })
        profile = StudentProfile.objects.get(user=self.student)
        self.assertEqual(profile.skills, 'Python, React')

    def test_invalid_phone_is_rejected(self):
        self.client.post('/profile/', {
            'full_name': 'Test',
            'email': 's@example.com',
            'phone': '12',
            'skills': '', 'headline': '', 'education': '',
            'projects': '', 'certifications': '',
        })
        profile = StudentProfile.objects.get(user=self.student)
        self.assertEqual(profile.phone, '')

    def test_recruiter_cannot_open_student_profile(self):
        self.client.logout()
        User.objects.create_user(username='rec', password='pw12345!',
                                 user_type='recruiter', email='r@example.com')
        self.client.login(username='rec', password='pw12345!')
        response = self.client.get('/profile/')
        self.assertEqual(response.status_code, 302)


class DashboardTests(TestCase):

    def test_student_gets_student_dashboard(self):
        User.objects.create_user(username='stu', password='pw12345!',
                                 user_type='student', email='s@example.com')
        self.client.login(username='stu', password='pw12345!')
        response = self.client.get('/dashboard/')
        self.assertTemplateUsed(response, 'student_dashboard.html')

    def test_recruiter_gets_recruiter_dashboard(self):
        User.objects.create_user(username='rec', password='pw12345!',
                                 user_type='recruiter', email='r@example.com')
        self.client.login(username='rec', password='pw12345!')
        response = self.client.get('/dashboard/')
        self.assertTemplateUsed(response, 'recruiter_dashboard.html')

    def test_admin_gets_admin_dashboard(self):
        User.objects.create_user(username='adm', password='pw12345!',
                                 user_type='admin', email='a@example.com')
        self.client.login(username='adm', password='pw12345!')
        response = self.client.get('/dashboard/')
        self.assertTemplateUsed(response, 'admin_dashboard.html')


class ProfileEmailValidationTests(TestCase):

    def test_profile_email_cannot_be_blank(self):
        user = User.objects.create_user(
            username='profileuser', password='pw12345!',
            email='profile@example.com', user_type='student')
        profile = StudentProfile.objects.create(user=user)
        form = ProfileForm(
            data={
                'email': '',
                'full_name': '',
                'phone': '',
                'headline': '',
                'skills': '',
                'education': '',
                'projects': '',
                'certifications': '',
            },
            instance=profile,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
