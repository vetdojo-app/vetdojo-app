from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import JournalArticleDetails


def _delete_private_file(storage, name):
    if name and storage.exists(name):
        storage.delete(name)


def _delete_replaced_private_file(article_id, storage, previous_name):
    # FileField finalizes upload_to and collision handling during model.save().
    # A replacement can therefore resolve to the same path as a missing old
    # file. Check the committed database value so that callback cannot delete
    # the newly uploaded replacement.
    current_name = (
        JournalArticleDetails.objects.filter(pk=article_id)
        .values_list("pdf_file", flat=True)
        .first()
    )
    if current_name != previous_name:
        _delete_private_file(storage, previous_name)


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
        lambda: _delete_replaced_private_file(
            instance.pk,
            previous.pdf_file.storage,
            previous.pdf_file.name,
        )
    )


@receiver(post_delete, sender=JournalArticleDetails)
def delete_removed_journal_pdf(sender, instance, **kwargs):
    if instance.pdf_file.name:
        transaction.on_commit(
            lambda: _delete_private_file(instance.pdf_file.storage, instance.pdf_file.name)
        )
