import os

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm

from .models import CustomUser, StudentProfile


class RegisterForm(UserCreationForm):

    email = forms.EmailField(
        required=True,
        help_text='Used for application and interview notifications.',
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'user_type', 'password1', 'password2']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'user_type': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('password1', 'password2'):
            self.fields[name].widget.attrs['class'] = 'form-control'
        # Accounts are never self-registered as admin.
        self.fields['user_type'].choices = [
            choice for choice in CustomUser.USER_TYPE_CHOICES
            if choice[0] != 'admin'
        ]

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if email and CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                'An account with this email already exists.')
        return email

    def clean_user_type(self):
        user_type = self.cleaned_data.get('user_type')
        if user_type not in ('student', 'recruiter'):
            raise forms.ValidationError('Choose Student or Recruiter.')
        return user_type

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class ResumeForm(forms.ModelForm):

    class Meta:
        model = StudentProfile
        fields = ['resume']
        widgets = {
            'resume': forms.ClearableFileInput(
                attrs={'class': 'form-control',
                       'accept': '.pdf,.png,.jpg,.jpeg'}),
        }

    def clean_resume(self):
        """
        Validate the uploaded file before it is ever written to disk or fed
        to the PDF/OCR pipeline.
        """
        resume = self.cleaned_data.get('resume')

        if not resume:
            raise forms.ValidationError('Please choose a file to upload.')

        # No new file was picked - keep the existing one.
        if not hasattr(resume, 'content_type') and not hasattr(resume, 'size'):
            return resume

        allowed = getattr(settings, 'RESUME_ALLOWED_EXTENSIONS',
                          ['.pdf', '.png', '.jpg', '.jpeg'])
        max_mb = getattr(settings, 'RESUME_MAX_SIZE_MB', 5)

        name = getattr(resume, 'name', '') or ''
        extension = os.path.splitext(name)[1].lower()

        if extension not in allowed:
            raise forms.ValidationError(
                'Unsupported file type. Allowed: {}.'.format(
                    ', '.join(allowed)))

        size = getattr(resume, 'size', 0) or 0
        if size > max_mb * 1024 * 1024:
            raise forms.ValidationError(
                f'File is too large. Maximum size is {max_mb} MB.')
        if size == 0:
            raise forms.ValidationError('That file appears to be empty.')

        content_type = (getattr(resume, 'content_type', '') or '').lower()
        allowed_types = {
            'application/pdf', 'application/x-pdf', 'application/octet-stream',
            'image/png', 'image/jpeg', 'image/jpg',
        }
        if content_type and content_type not in allowed_types:
            raise forms.ValidationError(
                'That file does not look like a PDF or an image.')

        # Check the real magic bytes rather than trusting the extension.
        try:
            position = resume.tell()
            resume.seek(0)
            header = resume.read(8)
            resume.seek(position)
        except (AttributeError, OSError):
            header = b''

        if header:
            is_pdf = header.startswith(b'%PDF')
            is_png = header.startswith(b'\x89PNG')
            is_jpg = header.startswith(b'\xff\xd8\xff')
            if extension == '.pdf' and not is_pdf:
                raise forms.ValidationError(
                    'That file has a .pdf name but is not a real PDF.')
            if extension in ('.png', '.jpg', '.jpeg') and not (is_png or is_jpg):
                raise forms.ValidationError(
                    'That file has an image name but is not a real image.')

        return resume


class ProfileForm(forms.ModelForm):

    email = forms.EmailField(
        required=True,
        help_text='Where interview and status emails are sent.',
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = StudentProfile
        fields = [
            'full_name', 'phone', 'headline', 'skills',
            'education', 'projects', 'certifications',
        ]
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'headline': forms.TextInput(attrs={'class': 'form-control'}),
            'skills': forms.Textarea(
                attrs={'class': 'form-control', 'rows': 3,
                       'placeholder': 'Python, Django, SQL'}),
            'education': forms.Textarea(
                attrs={'class': 'form-control', 'rows': 3}),
            'projects': forms.Textarea(
                attrs={'class': 'form-control', 'rows': 4,
                       'placeholder': 'One project per line'}),
            'certifications': forms.Textarea(
                attrs={'class': 'form-control', 'rows': 3,
                       'placeholder': 'One certification per line'}),
        }

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        if phone:
            digits = [c for c in phone if c.isdigit()]
            if len(digits) < 7 or len(digits) > 15:
                raise forms.ValidationError(
                    'Enter a valid phone number.')
        return phone

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return email
        qs = CustomUser.objects.filter(email__iexact=email)
        if self.instance and self.instance.user_id:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise forms.ValidationError(
                'Another account already uses this email.')
        return email
