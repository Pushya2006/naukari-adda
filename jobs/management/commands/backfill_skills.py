"""
Re-scan every stored resume and fill in StudentProfile.skills.

Needed once after the `skills` field was added: profiles uploaded before
that had their detected skills thrown away, which is why AI Match showed
0% for them.

    python manage.py backfill_skills
"""

from django.core.management.base import BaseCommand

from jobs.matching import extract_skills_from_text, parse_skill_list
from users.models import StudentProfile


class Command(BaseCommand):
    help = 'Populate StudentProfile.skills from stored resume text.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Replace existing skills instead of merging into them.',
        )

    def handle(self, *args, **options):

        overwrite = options['overwrite']
        updated = skipped = 0

        for profile in StudentProfile.objects.all():

            text = (profile.resume_text or '').strip()

            if not text:
                skipped += 1
                continue

            detected = extract_skills_from_text(text)

            if not detected:
                skipped += 1
                continue

            if overwrite:
                merged = detected
            else:
                existing = parse_skill_list(profile.skills or '')
                merged = existing + [s for s in detected if s not in existing]

            new_value = ', '.join(merged)

            if new_value != (profile.skills or ''):
                profile.skills = new_value
                profile.save(update_fields=['skills', 'updated_at'])
                updated += 1
                self.stdout.write(
                    f'  {profile.user.username}: {new_value}')
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done. {updated} profile(s) updated, {skipped} skipped.'))
