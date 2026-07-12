"""Seed a rich set of development data.

Creates users across every role, several courses (active and inactive, with varied scoring
policies, grade bands, and thumbnails), published and draft assignments, problems, Lean source
files, and graded submissions — enough to exercise the dashboards, rosters, grade cards, the
courses search, the active/previous split, and the live Lean editor. Idempotent: re-running
tops up anything missing rather than duplicating.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.homework.models import (
    Assignment,
    Course,
    LeanSourceFile,
    Problem,
    ProblemBlock,
    Submission,
)

User = get_user_model()

# Every seeded user shares this password.
PASSWORD = "password"

DEFINITION_CONTENT = """/-- Our copy of the natural numbers called `MyNat`, with notation `ℕ`. -/
inductive MyNat where
  | zero : MyNat
  | succ : MyNat → MyNat

attribute [pp_nodot] MyNat.succ

notation:1000000 "ℕ" => MyNat

namespace MyNat

instance : Inhabited MyNat where
  default := MyNat.zero

def ofNat : Nat → MyNat
  | Nat.zero   => MyNat.zero
  | Nat.succ b => MyNat.succ (ofNat b)

def toNat : MyNat → Nat
  | MyNat.zero   => Nat.zero
  | MyNat.succ b => Nat.succ (toNat b)

instance instOfNat (n : Nat) : OfNat MyNat n where
  ofNat := ofNat n

instance : ToString MyNat where
  toString p := toString (toNat p)

theorem zero_eq_0 : MyNat.zero = (0 : MyNat) := rfl

def one : MyNat := MyNat.succ (0 : MyNat)

end MyNat
"""

ADDITION_CONTENT = """namespace MyNat

opaque add : MyNat → MyNat → MyNat

instance instAdd : Add MyNat where
  add := MyNat.add

/--
`add_zero a` is a proof of `a + 0 = a`.

`add_zero` is a `simp` lemma, because if you see `a + 0`
you usually want to simplify it to `a`.
-/
@[simp]
axiom add_zero (a : MyNat) : a + (0 : MyNat) = a

/--
If `a` and `d` are natural numbers, then `add_succ a d` is the proof that
`a + succ d = succ (a + d)`.
-/
axiom add_succ (a d : MyNat) :
  a + MyNat.succ d = MyNat.succ (a + d)

end MyNat
"""

TRIVIAL_CODE = "example : True := trivial\n"


class Command(BaseCommand):
    help = "Seed development users, courses, assignments, problems, and submissions."

    def handle(self, *args, **options):
        # region Users (all share the password "password")
        teacher = self._user("teacher", is_staff=True)  # site admin
        instructor = self._user("instructor")  # course instructor, not an admin
        ta = self._user("ta")  # read-only course staff
        student = self._user(
            "student"
        )  # enrolled in everything; gets a spread of grades
        student2 = self._user("student2")  # aces Intro to Lean
        student3 = self._user("student3")  # fails Intro to Lean
        # endregion

        # region Lean source files imported by the MyNat problem
        definition_file = self._source_file(
            "mynat-definition", "MyNat Definition", DEFINITION_CONTENT, teacher
        )
        addition_file = self._source_file(
            "mynat-addition", "MyNat Addition", ADDITION_CONTENT, teacher
        )
        # endregion

        # region Course 1: Intro to Lean (active, the main course)
        intro = self._course(
            "intro-lean",
            "Intro to Lean",
            "Build the natural numbers in Lean 4 and prove basic facts about them.",
            instructors=[teacher, instructor],
            tas=[ta],
            students=[student, student2, student3],
            scoring=Course.SCORING_BEST,
            thumbnail_preset="aurora.svg",
        )
        foundations = self._assignment(
            intro,
            "foundations",
            "Foundations",
            teacher,
            published=True,
            source_files=[definition_file, addition_file],
        )
        p1 = self._mynat_problem(foundations, definition_file, addition_file)
        p2 = self._simple_problem(foundations, "Successor facts", 2, TRIVIAL_CODE)
        # A draft (unpublished) assignment drives the staff card's "drafts" badge.
        self._assignment(
            intro, "wip-induction", "Induction (draft)", teacher, published=False
        )
        # endregion

        # region Course 2: Proofs 101 (active, recent-submission scoring)
        proofs = self._course(
            "proofs-101",
            "Proofs 101",
            "A gentle introduction to writing proofs with tactics.",
            instructors=[instructor],
            students=[student],
            scoring=Course.SCORING_RECENT,
            thumbnail_preset="meadow.svg",
        )
        logic = self._assignment(
            proofs, "logic-basics", "Logic basics", instructor, published=True
        )
        p_logic = self._simple_problem(logic, "And / Or", 1, TRIVIAL_CODE)
        # endregion

        # region Course 3: Legacy Logic (inactive -> "Previous courses", custom grade bands)
        legacy = self._course(
            "legacy-logic",
            "Legacy Logic",
            "An older course, kept around for reference.",
            instructors=[instructor],
            students=[student],
            scoring=Course.SCORING_SUPERSCORE,
            thumbnail_preset="ember.svg",
            is_active=False,
            grade_bands=(85, 75, 65, 55),
        )
        archived = self._assignment(
            legacy, "archived-set", "Archived problem set", instructor, published=True
        )
        p_legacy = self._simple_problem(
            archived, "Propositional logic", 1, TRIVIAL_CODE
        )
        # endregion

        # region Submissions -> grades
        # student: F in Intro (1/2), A in the other two -> shows the full colour range and an
        # open assignment. student2 aces Intro, student3 fails it -> real class averages.
        self._submit(student, p1, Submission.STATUS_PASSED)
        self._submit(student, p2, Submission.STATUS_FAILED)
        self._submit(student2, p1, Submission.STATUS_PASSED)
        self._submit(student2, p2, Submission.STATUS_PASSED)
        self._submit(student3, p1, Submission.STATUS_FAILED)
        self._submit(student3, p2, Submission.STATUS_FAILED)
        self._submit(student, p_logic, Submission.STATUS_PASSED)
        self._submit(student, p_legacy, Submission.STATUS_PASSED)
        # endregion

        self.stdout.write(
            self.style.SUCCESS(
                "\nSeeded 6 users (password 'password'), 3 courses, 4 assignments, "
                "4 problems, and submissions.\n"
                "Sign in as: teacher (admin), instructor, ta, student / student2 / student3."
            )
        )

    # -- helpers --------------------------------------------------------------------------

    def _user(self, username, is_staff=False):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@example.com", "is_staff": is_staff},
        )
        if created:
            user.set_password(PASSWORD)
            user.save()
        return user

    def _source_file(self, slug, title, content, owner):
        source_file, _ = LeanSourceFile.objects.get_or_create(
            slug=slug,
            defaults={"title": title, "content": content, "created_by": owner},
        )
        return source_file

    def _course(
        self,
        slug,
        title,
        description,
        *,
        instructors=(),
        tas=(),
        students=(),
        scoring=Course.SCORING_BEST,
        thumbnail_preset="",
        is_active=True,
        grade_bands=None,
    ):
        defaults = {
            "title": title,
            "description": description,
            "scoring_method": scoring,
            "thumbnail_preset": thumbnail_preset,
            "is_active": is_active,
        }
        if grade_bands:
            a, b, c, d = grade_bands
            defaults.update(grade_a_min=a, grade_b_min=b, grade_c_min=c, grade_d_min=d)
        course, _ = Course.objects.get_or_create(slug=slug, defaults=defaults)
        course.instructors.add(*instructors)
        course.tas.add(*tas)
        course.students.add(*students)
        return course

    def _assignment(self, course, slug, title, owner, *, published, source_files=()):
        assignment, _ = Assignment.objects.get_or_create(
            course=course,
            slug=slug,
            defaults={
                "title": title,
                "description": f"{title} for {course.title}.",
                "created_by": owner,
                "is_published": published,
            },
        )
        if source_files:
            assignment.source_files.add(*source_files)
        return assignment

    def _simple_problem(self, assignment, title, order, code):
        """A minimal single-editable-block problem (enough to render the editor and grade)."""
        problem, _ = Problem.objects.get_or_create(
            assignment=assignment,
            title=title,
            defaults={
                "statement": f"{title}: practice problem.",
                "required_code": "",
                "grading_stub": "",
                "order": order,
                "points": 1,
            },
        )
        if not problem.blocks.exists():
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
                content=code,
                order=0,
            )
        return problem

    def _mynat_problem(self, assignment, definition_file, addition_file):
        """The rich MyNat problem used to demo live Lean feedback (prefix/fixed/editable)."""
        problem, _ = Problem.objects.get_or_create(
            assignment=assignment,
            title="MyNat Addition",
            defaults={
                "statement": "Use the MyNat definitions and addition axioms to prove a + 0 = a.",
                "required_code": "",
                "grading_stub": "",
                "order": 1,
                "points": 1,
            },
        )
        if not problem.blocks.exists():
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_TEXT,
                content="This problem imports the `MyNat` definition and addition axioms.",
                order=0,
            )
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_FIXED_CODE,
                content="open MyNat\n\n-- You can use `simp` with `add_zero` and `add_succ`.\n",
                order=1,
            )
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
                content="theorem add_zero' (a : MyNat) : a + 0 = a := by\n  simp\n",
                order=2,
            )
        problem.visible_source_files.set([definition_file, addition_file])
        return problem

    _RESULTS = {
        Submission.STATUS_PASSED: "Lean ran with no errors :)",
        Submission.STATUS_FAILED: "error: unsolved goals\n⊢ a + 0 = a",
        Submission.STATUS_ERROR: "Lean execution timed out.",
    }

    def _submit(self, user, problem, status):
        Submission.objects.get_or_create(
            user=user,
            problem=problem,
            defaults={
                "code": "-- seeded submission\n",
                "status": status,
                "result": self._RESULTS.get(status, ""),
            },
        )
