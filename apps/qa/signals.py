from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import JournalArticleDetails


def _delete_private_file(storage, name):
    if name and storage.exists(name):
        storage.delete(name)


@receiver(pre_save, sender=JournalArticleDetails)
def delete_replaced_journal_pdf(sender, instance, **kwargs):
    if not instance.pk:
        return
    previous = sender.objects.filter(pk=instance.pk).only("pdf_file").first()
    if not previous or not previous.pdf_file.name:
        return
    if previous.pdf_file.name == instance.pdf_file.name:
        return
    transaction.on_commit(
        lambda: _delete_private_file(previous.pdf_file.storage, previous.pdf_file.name)
    )


@receiver(post_delete, sender=JournalArticleDetails)
def delete_removed_journal_pdf(sender, instance, **kwargs):
    if instance.pdf_file.name:
        transaction.on_commit(
            lambda: _delete_private_file(instance.pdf_file.storage, instance.pdf_file.name)
        )
