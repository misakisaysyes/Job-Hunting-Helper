"""New greeting highlights use exact header fields and never enter a chat."""

import json
import shutil
import subprocess
import unittest

from browser.browser import _BOSS_CHAT_HIGHLIGHT_ROW


@unittest.skipUnless(shutil.which("node"), "Node is needed to evaluate the row highlighter")
class NewGreetingRowTests(unittest.TestCase):
    def highlight(self, rows):
        script = """
            const fs = require('fs');
            const f = JSON.parse(fs.readFileSync(0, 'utf8'));
            global.location = {href: 'https://www.zhipin.com/web/geek/chat'};
            const nodes = f.rows.map(row => ({
                innerText: row.text || '', style: {}, scrolled: false,
                querySelector: selector => selector === 'img.image-circle' ? {src: row.avatar}
                    : selector === '.name-text' ? {textContent: row.name}
                    : selector === '.name-box > span:nth-child(2)' ? {textContent: row.brand} : null,
                scrollIntoView() { this.scrolled = true; },
                click() { throw new Error('Entering a new greeting is forbidden in this action'); },
            }));
            global.document = {querySelectorAll: () => nodes};
            const result = eval('(' + f.source + ')')({avatar: 'https://example.com/avatar.png',
                name: '李女士', brand: '示例公司', exactIdentity: true});
            process.stdout.write(JSON.stringify({result, rows: nodes.map(node => ({
                style: node.style, scrolled: node.scrolled,
            }))}));
        """
        result = subprocess.run([shutil.which("node"), "-e", script],
            input=json.dumps({"source": _BOSS_CHAT_HIGHLIGHT_ROW, "rows": rows}),
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_exact_recruiter_and_company_headers_are_highlighted_without_clicking(self):
        result = self.highlight([{"avatar": "//example.com/avatar.png?size=large",
                                  "name": " 李女士 ", "brand": " 示例公司 "}])
        self.assertEqual(result["result"], "located")
        self.assertTrue(result["rows"][0]["scrolled"])
        self.assertEqual(result["rows"][0]["style"]["backgroundColor"], "#edf5ff")

    def test_name_or_company_in_a_message_or_as_a_substring_cannot_select_a_row(self):
        for name, brand in (("王先生", "其他公司"), ("李女士1", "示例公司"),
                            ("李女士", "示例公司子公司")):
            with self.subTest(name=name, brand=brand):
                result = self.highlight([{"avatar": "https://example.com/avatar.png",
                    "name": name, "brand": brand, "text": "李女士 示例公司 您好"}])
                self.assertEqual(result["result"], "missing")
                self.assertEqual(result["rows"], [{"scrolled": False, "style": {}}])

    def test_identical_rows_are_left_unchanged(self):
        row = {"avatar": "https://example.com/avatar.png", "name": "李女士", "brand": "示例公司"}
        result = self.highlight([row, row])
        self.assertEqual(result["result"], "ambiguous")
        self.assertEqual(result["rows"], [{"scrolled": False, "style": {}}] * 2)


if __name__ == "__main__":
    unittest.main()
