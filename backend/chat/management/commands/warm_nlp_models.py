from django.core.management.base import BaseCommand

from chat.services.embedding import get_embedding
from chat.services.emotion import analyze_emotion


class Command(BaseCommand):
    help = "Download and warm up NLP models before serving chat traffic."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-embedding",
            action="store_true",
            help="Do not preload the multilingual sentence embedding model.",
        )
        parser.add_argument(
            "--skip-emotion",
            action="store_true",
            help="Do not preload the H-H emotion analysis model.",
        )

    def handle(self, *args, **options):
        if options["skip_embedding"]:
            self.stdout.write("Embedding model skipped")
        else:
            self.stdout.write("Loading embedding model...")
            vector = get_embedding("台灣核能議題討論")
            self.stdout.write(self.style.SUCCESS(
                f"Embedding model ready ({len(vector)} dimensions)"
            ))

        if options["skip_emotion"]:
            self.stdout.write("Emotion model skipped")
        else:
            self.stdout.write("Loading emotion model...")
            result = analyze_emotion("這是一段平和的測試文字。")
            label = result.get("label", "unknown")
            score = result.get("score", 0.0)
            self.stdout.write(self.style.SUCCESS(
                f"Emotion model ready (label={label}, score={score})"
            ))
