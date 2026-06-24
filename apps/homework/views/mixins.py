from django.contrib.auth.mixins import UserPassesTestMixin

from ..models import Course


class StaffRequiredMixin(UserPassesTestMixin):
    """Restrict a view to site admins (Django staff) — e.g. creating courses."""

    def test_func(self):
        return bool(self.request.user.is_staff)


class InstructorAnywhereMixin(UserPassesTestMixin):
    """Allow site admins and anyone who instructs at least one course — used for the shared
    Lean source-file library."""

    def test_func(self):
        user = self.request.user
        return bool(user.is_staff) or Course.objects.filter(instructors=user).exists()


class FormsetMixin:
    """Manage one related inline formset alongside the main form on a Create/Update view.

    Set ``formset_class`` and ``formset_context_name``. The formset is built bound to POST
    data or to the object instance, exposed in the template context, and — when both it and
    the main form validate — saved against the just-saved object. Views that need to stamp
    fields on the new instance (e.g. ``form.instance.created_by``) set them in their own
    ``form_valid`` before calling ``super().form_valid(form)``.
    """

    formset_class = None
    formset_context_name = "formset"

    def get_formset(self):
        instance = getattr(self, "object", None)
        if self.request.method == "POST":
            return self.formset_class(self.request.POST, instance=instance)
        return self.formset_class(instance=instance)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault(self.formset_context_name, self.get_formset())
        return context

    def form_valid(self, form):
        formset = self.get_formset()
        if not formset.is_valid():
            return self.form_invalid(form)
        self.object = form.save()
        formset.instance = self.object
        formset.save()
        return super().form_valid(form)
