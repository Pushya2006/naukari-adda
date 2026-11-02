
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, call_command, CommandError

from quiz.models import Question


class Command(BaseCommand):
    help = "Import quiz questions from the JSON fixture"

    def handle(self, *args, **options):
        fixture_path = Path(settings.BASE_DIR) / "quiz_questions.json"

        if Question.objects.exists():
            self.stdout.write(
                self.style.WARNING("Quiz questions already exist. Skipping import.")
            )
            return

        if not fixture_path.is_file():
            raise CommandError("quiz_questions.json was not found.")

        call_command("loaddata", str(fixture_path), verbosity=0)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully imported {Question.objects.count()} quiz questions."
            )
        )
