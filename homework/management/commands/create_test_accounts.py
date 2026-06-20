"""Django management command to create test user and teacher accounts."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from homework.models import Assignment, Course, LeanSourceFile, Problem, ProblemBlock

User = get_user_model()


class Command(BaseCommand):
    help = "Create test user and teacher accounts"

    def handle(self, *args, **options):
        # Create test teacher if not exists
        teacher, created = User.objects.get_or_create(
            username="teacher",
            defaults={"email": "teacher@example.com", "is_staff": True},
        )
        if created:
            teacher.set_password("password")
            teacher.save()
            self.stdout.write(
                self.style.SUCCESS(
                    "Created teacher account: username=teacher, password=password"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("Teacher account already exists"))

        # Create test student if not exists
        student, created = User.objects.get_or_create(
            username="student", defaults={"email": "student@example.com"}
        )
        if created:
            student.set_password("password")
            student.save()
            self.stdout.write(
                self.style.SUCCESS(
                    "Created student account: username=student, password=password"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("Student account already exists"))

        self.stdout.write(self.style.SUCCESS("\nTest accounts ready!"))

        # Create a default course, assignment, and problem for testing
        course, created = Course.objects.get_or_create(
            slug="intro-lean",
            defaults={
                "title": "Intro to Lean",
                "description": "A small test course",
                "instructor": teacher,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created course: Intro to Lean"))
        else:
            self.stdout.write(self.style.WARNING("Course Intro to Lean already exists"))

        # Enroll the test student
        if student not in course.students.all():
            course.students.add(student)
            self.stdout.write(self.style.SUCCESS("Enrolled student in Intro to Lean"))

        assignment, created = Assignment.objects.get_or_create(
            course=course,
            slug="test-assignment",
            defaults={
                "title": "Test Assignment",
                "description": "Automatically-created assignment for testing",
                "created_by": teacher,
                "is_published": True,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created assignment: Test Assignment"))
        else:
            self.stdout.write(
                self.style.WARNING("Assignment Test Assignment already exists")
            )

        definition_content = """/-- Our copy of the natural numbers called `MyNat`, with notation `ℕ`. -/
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

        addition_content = """namespace MyNat

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

        definition_file, created = LeanSourceFile.objects.get_or_create(
            slug="mynat-definition",
            defaults={
                "title": "MyNat Definition",
                "content": definition_content,
                "visible": True,
                "created_by": teacher,
            },
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS("Created source file: MyNat Definition")
            )

        addition_file, created = LeanSourceFile.objects.get_or_create(
            slug="mynat-addition",
            defaults={
                "title": "MyNat Addition",
                "content": addition_content,
                "visible": True,
                "created_by": teacher,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created source file: MyNat Addition"))

        if definition_file not in assignment.source_files.all():
            assignment.source_files.add(definition_file)
        if addition_file not in assignment.source_files.all():
            assignment.source_files.add(addition_file)

        problem, created = Problem.objects.get_or_create(
            assignment=assignment,
            title="MyNat Addition",
            defaults={
                "statement": "Use the MyNat definitions and addition axioms to prove a simple property.",
                "required_code": "",
                "grading_stub": "",
                "order": 1,
                "points": 1,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created problem: MyNat Addition"))
        else:
            self.stdout.write(
                self.style.WARNING("Problem MyNat Addition already exists")
            )

        # Create example blocks if none exist
        if not problem.blocks.exists():
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_TEXT,
                content="This problem uses `Game.MyNat.Definition` and `Game.MyNat.Addition` as imported source files.",
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
            self.stdout.write(
                self.style.SUCCESS("Added example blocks to MyNat Addition")
            )

        self.stdout.write(
            self.style.SUCCESS("\nDefault course/assignment/problem ready for testing")
        )
