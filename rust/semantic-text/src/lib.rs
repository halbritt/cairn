//! Text predicates for Cairn's frozen semantic/4 input contract.

/// Returns whether the input is empty or contains only Python 3.11 whitespace.
///
/// Uses the fixed semantic/4 scalar set without allocating, normalizing or
/// changing the input.
pub fn is_python_blank(text: &str) -> bool {
    text.chars().all(is_python_whitespace)
}

fn is_python_whitespace(scalar: char) -> bool {
    matches!(
        scalar,
        '\u{0009}'..='\u{000d}'
            | '\u{001c}'..='\u{0020}'
            | '\u{0085}'
            | '\u{00a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{2028}'..='\u{2029}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}'
    )
}

#[cfg(test)]
mod tests {
    use super::is_python_blank;

    // Exact existing fixture, independent of the implementation's ranges:
    // scripts/semantic_fixture_cases.py:10-12, blob
    // b6ff4e33b4f9b9097dbf49a6abfcf1490a9271f2.
    const PYTHON_WHITESPACE_CODEPOINTS: [u32; 29] = [
        9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197,
        8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288,
    ];

    #[test]
    fn empty_and_all_frozen_whitespace_are_blank() {
        assert!(is_python_blank(""));
        let mut combined = String::new();
        for codepoint in PYTHON_WHITESPACE_CODEPOINTS {
            let scalar = char::from_u32(codepoint).expect("fixture contains Unicode scalars");
            let mut encoded = [0; 4];
            assert!(
                is_python_blank(scalar.encode_utf8(&mut encoded)),
                "U+{codepoint:04X} must be blank"
            );
            combined.push(scalar);
        }
        assert!(is_python_blank(&combined));
    }

    #[test]
    fn any_nonwhitespace_makes_input_nonblank() {
        for text in [
            "\0",
            "\u{0008}",
            "\u{000e}",
            "\u{001b}",
            "!",
            "\u{0084}",
            "\u{0086}",
            "\u{009f}",
            "\u{00a1}",
            "\u{167f}",
            "\u{1681}",
            "\u{180e}",
            "\u{1fff}",
            "\u{200b}",
            "\u{200d}",
            "\u{2027}",
            "\u{202a}",
            "\u{202e}",
            "\u{2030}",
            "\u{205e}",
            "\u{2060}",
            "\u{2fff}",
            "\u{3001}",
            "\u{feff}",
            "ASCII text",
            "漢字",
            "🙂",
            "e\u{0301}",
            "\u{0301}",
            "ASCII\t漢字🙂e\u{0301}",
        ] {
            for candidate in [
                text.to_owned(),
                format!("{text}\u{001c}\u{3000}"),
                format!("\t\u{001f}{text}"),
                format!("\u{0085}{text}\u{00a0}"),
            ] {
                assert!(
                    !is_python_blank(&candidate),
                    "nonblank input rejected: {candidate:?}"
                );
            }
        }
    }
}
