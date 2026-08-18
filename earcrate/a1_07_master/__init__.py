"""A1-07 delivery mastering: the stage after the owner-selected frontier.

This is a separate package from `a1_07_full_form` on purpose, and the reason is
provenance rather than tidiness.

`a1_07_full_form.provenance` digests every tracked file under the full-form
package because any of them can change a rendered candidate. The accepted
native-pocket render is bound to that digest. Mastering runs *after* the render
and cannot alter one sample of it, so putting the mastering chain inside the
full-form package would move the adapter digest, contradict the manifest the
accepted render already carries, and drop the lane to `representative_invocation_
ready = False` -- forcing a re-render to re-prove something the change could not
have touched. That is precisely the false alarm the digest exists to prevent.

So the boundary is structural: mastering imports from the full-form package and
never lives inside it, and it carries its own digest over its own files.
"""

from .chain import CEILING_DBTP, MasteringError

__all__ = ["CEILING_DBTP", "MasteringError"]
