import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from metadata_reader import _xmp_fields

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


class WorkflowsArePinned(unittest.TestCase):
    def test_every_action_is_pinned_to_a_full_commit(self):
        seen = 0
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                match = re.match(r"\s*(?:-\s*)?uses:\s*(\S+)", line)
                if not match or match.group(1).startswith("./"):
                    continue
                seen += 1
                self.assertRegex(match.group(1), r"@[0-9a-f]{40}$", f"{workflow.name}: {line.strip()}")
        self.assertGreater(seen, 0)

    def test_only_the_publishing_job_can_write_to_the_repository(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        top_level = re.search(r"^permissions:\n  contents: (\w+)", text, re.MULTILINE)
        self.assertEqual(top_level.group(1), "read")
        self.assertEqual(len(re.findall(r"contents: write", text)), 1)
        self.assertRegex(text, r"release:\n(?:.*\n)*?    permissions:\n      contents: write")


class EmbeddedXmlIsNotExpanded(unittest.TestCase):
    PACKET = (
        b'http://ns.adobe.com/xap/1.0/\x00{doctype}<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        b'xmlns:dc="http://purl.org/dc/elements/1.1/"><rdf:Description>'
        b'<dc:title>{title}</dc:title></rdf:Description></rdf:RDF></x:xmpmeta>'
    )

    def image(self, doctype=b"", title=b"Plain title"):
        payload = self.PACKET.replace(b"{doctype}", doctype).replace(b"{title}", title)
        return SimpleNamespace(applist=[("APP1", payload)])

    def test_an_ordinary_block_is_read(self):
        self.assertEqual(_xmp_fields(self.image()), {"Title": "Plain title"})

    def test_a_block_declaring_entities_is_skipped(self):
        bomb = b'<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
        self.assertEqual(_xmp_fields(self.image(doctype=bomb, title=b"&b;")), {})
        self.assertEqual(_xmp_fields(self.image(doctype=b"<!DOCTYPE x>")), {})


if __name__ == "__main__":
    unittest.main()
