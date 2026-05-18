RECOVERY_PROMPT = """You are given the source code of a file and a corresponding diff patch that reflects changes made to this file.
Your task is to rewrite the entire source code while reversing the changes indicated by the diff patch.
That is, if a line was added in the diff, remove it; if a line was removed, add it back; and if a line was modified, restore it to its previous state.

DO NOT MAKE ANY OTHER CHANGES TO THE SOURCE CODE. If a line was not explicitly added or removed in the diff, it should remain unchanged in the output.

INPUT:
<source_code>
Source code will be provided here.
</source_code>

<diff_patch>
Diff patch will be provided here.
</diff_patch>

OUTPUT:
The fully rewritten source code, after undoing all changes specified in the diff.
The output should be valid code (Python or Rust depending on the input file).
"""

DEMO_PROMPT = """Demonstration:

INPUT:
<source_code>
def greet(name):
    print(f"Hi, {name}! How's it going?")
    print("Even though this line is not in the diff, it should remain unchanged.")

def farewell(name):
    print(f"Goodbye, {name}!")
</source_code>

<diff_patch>
diff --git a/greet.py b/greet.py
index 1234567..7654321 100644
--- a/greet.py
+++ b/greet.py
@@ -1,4 +1,4 @@
 def greet(name):
-    print(f"Hello, {name}! How are you?")
+    print(f"Hi, {name}! How's it going?")

 def farewell(name):
     print(f"Goodbye, {name}!")
</diff_patch>
</input>

OUTPUT:
def greet(name):
    print(f"Hello, {name}! How are you?")
    print("Even though this line is not in the diff, it should remain unchanged.")

def farewell(name):
    print(f"Goodbye, {name}!")
"""

TASK_PROMPT = """Task:

INPUT:
<source_code>
{}
</source_code>

<diff_patch>
{}
</diff_patch>
</input>

NOTES:
- As a reminder, DO NOT MAKE ANY OTHER CHANGES TO THE SOURCE CODE. If a line was not explicitly added or removed in the diff, it should remain unchanged in the output.
- Only make changes based on lines that were:
    * Added (have a + in front of them)
    * Removed (have a - in front of them)
- DO NOT PROVIDE ANY TEXT ASIDE FROM THE REWRITTEN FILE. ANSWER WITH ONLY THE REWRITTEN CODE.

OUTPUT:"""

CHUNKED_RECOVERY_PROMPT = """You are given a SECTION of source code and one or more diff hunks that reflect changes made to this file.
The diff was originally created against an older version of the file, so line numbers may not match exactly and surrounding code may have drifted.
Your task is to rewrite ONLY this section while reversing the changes indicated by the diff hunks.
That is, if a line was added in the diff, remove it; if a line was removed, add it back; and if a line was modified, restore it to its previous state.

Identify the corresponding code by matching content, variable names, function signatures, and logic flow from the diff — do not rely on exact line numbers.
DO NOT MAKE ANY OTHER CHANGES TO THE SOURCE CODE. If a line was not explicitly added or removed in the diff, it should remain unchanged in the output.

INPUT:
<section>
Source section will be provided here.
</section>

<diff_hunks>
Diff hunks will be provided here.
</diff_hunks>

OUTPUT:
The fully rewritten section, after undoing all changes specified in the diff.
The output should be valid code (Python or Rust depending on the input file).
"""

CHUNKED_DEMO_PROMPT = """Demonstration:

SECTION:
<source_code>
def greet(name):
    print(f"Hi, {name}! How's it going?")
    print("Even though this line is not in the diff, it should remain unchanged.")

def farewell(name):
    print(f"Goodbye, {name}!")
</source_code>

DIFF HUNK:
@@ -1,4 +1,4 @@
 def greet(name):
-    print(f"Hello, {name}! How are you?")
+    print(f"Hi, {name}! How's it going?")

 def farewell(name):
     print(f"Goodbye, {name}!")

OUTPUT:
def greet(name):
    print(f"Hello, {name}! How are you?")
    print("Even though this line is not in the diff, it should remain unchanged.")

def farewell(name):
    print(f"Goodbye, {name}!")
"""

CHUNKED_TASK_PROMPT = """Task:

SECTION:
<source_code>
{}
</source_code>

DIFF HUNKS:
{}
</diff_hunks>

NOTES:
- The diff line numbers may not match the section exactly because the code has evolved. Match changes by content and structure, not by line numbers.
- DO NOT MAKE ANY OTHER CHANGES TO THE SOURCE CODE. If a line was not explicitly added or removed in the diff, it should remain unchanged in the output.
- Only make changes based on lines that were:
    * Added (have a + in front of them)
    * Removed (have a - in front of them)
- DO NOT PROVIDE ANY TEXT ASIDE FROM THE REWRITTEN SECTION. ANSWER WITH ONLY THE REWRITTEN CODE.

OUTPUT:"""
