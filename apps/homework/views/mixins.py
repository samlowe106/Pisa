from typing import TYPE_CHECKING

from django.contrib.auth.mixins import UserPassesTestMixin

from ..models import Course

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.forms.models import BaseInlineFormSet


class StaffRequiredMixin(UserPassesTestMixin):
    """Restrict a view to site admins (Django staff), e.g. creating courses."""

    # No return/request annotations here: django-stubs types HttpRequest.user as
    # User | AnonymousUser, but this mixin is only ever combined with LoginRequiredMixin
    # (which the type system can't see), so a precise annotation needs a shared
    # AuthenticatedHttpRequest convention, not a one-file fix.
    def test_func(self):
        return bool(self.request.user.is_staff)


class InstructorAnywhereMixin(UserPassesTestMixin):
    """Allow site admins and anyone who instructs at least one course; used for the shared
    Lean source-file library."""

    def test_func(self):
        user = self.request.user
        return bool(user.is_staff) or Course.objects.filter(instructors=user).exists()


class ResolvedObjectMixin:
    """``get_object()`` for views whose object is found via nested URL kwargs, not pk/slug.

    Set ``object_resolver = staticmethod(resolver)`` where ``resolver(queryset, url_kwargs)``
    returns the object (404ing itself as appropriate).
    """

    object_resolver: Callable | None = None

    # Not return-typed: like FormsetMixin below, this bare mixin's self.get_queryset()/
    # self.kwargs only resolve once combined with a real View subclass.
    def get_object(self, queryset=None):
        return self.object_resolver(
            queryset if queryset is not None else self.get_queryset(), self.kwargs
        )


class FormsetMixin:
    """Manage one related inline formset alongside the main form on a Create/Update view.

    Set ``formset_class`` and ``formset_context_name``. The formset is built bound to POST
    data or to the object instance, exposed in the template context, and saved against the
    just-saved object when both it and the main form validate. Views that need to stamp
    fields on the new instance (e.g. ``form.instance.created_by``) set them in their own
    ``form_valid`` before calling ``super().form_valid(form)``.
    """

    formset_class: type[BaseInlineFormSet] | None = None
    formset_context_name = "formset"

    # Not return-typed: FormsetMixin is a bare mixin (no base class), always combined with a
    # real View subclass at actual usage sites. `self.request`/`self.form_invalid`/
    # `super().get_context_data(...)`/`super().form_valid(...)` only resolve at runtime via
    # that combination, which mypy can't see from this file alone.
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
