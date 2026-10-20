# Licensing

Copyright in the project's original contributions belongs to Dave (GitHub:
[`dkjroot`](https://github.com/dkjroot)) and the respective contributors.
Third-party authors retain copyright in their own work.

Except for separately identified third-party components and assets, this
project's original code and documentation are free software: you may redistribute
them and/or modify them under the **GNU General Public License, version 3**
(`GPL-3.0-only`), published by the Free Software Foundation. The complete license
is in [LICENSE](LICENSE). The software is provided without warranty;
see that license for details. This notice grants version 3, without an
"or any later version" option.

## The maintainer's request and your legal rights

The README asks people to keep the software freely available, avoid profiting
from paid copies or services built around it, use the official GitHub repository,
and send voluntary development support directly to the maintainer. These are
requests, not added license conditions. Commercial use, charging for copies,
lawful forks, and redistribution remain permitted by GPLv3. Recipients keep
their license rights; donations and maintainer approval are not prerequisites.

When conveying covered software, comply with the GPL's applicable notice,
licensing, and corresponding-source requirements. A GitHub link alone is not
necessarily enough for a binary distribution. Merely running a hosted service
does not by itself trigger the GPL's source-distribution requirements. See the
[GNU licensing FAQ](https://www.gnu.org/licenses/gpl-faq.html).

Third-party packages, data, media, model weights, and external services retain
their own terms. A dependency's license label or this document does not replace
its actual license. Preserve all applicable copyright notices, license texts,
attributions, and any required source or change notices when redistributing them.
Optional external services may charge users independently of this free software.

## Dependencies, models, and sounds

The current application's dependencies are declared in `pyproject.toml` and
resolved in `uv.lock`. The source-import inventory and upstream license references are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The identified library licenses
permit use with GPLv3, subject to their own notice and redistribution obligations.
This is a source review, not approval of an arbitrary future environment or binary
bundle. Check the exact versions, transitive packages, and native libraries before
shipping an installer.

Model weights, operating-system voices, externally run LLM servers, and online
speech/chat services are separate from IRIS's code license. Their terms still
apply. The configured Jonatas Grosman Wav2Vec2 English model declares Apache-2.0;
other user-selected models need their own review.

The four old MP3 cues were removed because their provenance could not be
established. New procedural WAV cues, their generator, and replacement guidance
are documented in [iris_sounds/README.md](iris_sounds/README.md). Do not restore
the old MP3s from history on the assumption that IRIS's GPL grants rights to them.

This review covers the current checked-out branch. The historical `prototypes`
branch contains additional examples, model references, and old MP3 copies. Neither
that branch nor Git history has been rewritten or cleared for redistribution by
this review; review or omit them when preparing a public repository.
