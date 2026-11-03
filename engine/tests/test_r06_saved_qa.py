"""Synthetic saved-page follow-ups: host fields, support IDs and software names."""
from docsan import verify
from docsan.match import Matcher
from docsan.profile import Profile, Term


def test_vm_machine_and_backup_host_contexts():
    matcher = Matcher(Profile("Example", "v1", []), set())
    for value in ("VM SEABRIGHTA02 in UAT", "Machine: EXAMPLEDBP01",
                  "Backup DB in production EXAMPLEDBP01"):
        assert any(hit.label == "DOMAIN" for hit in matcher.find(value).hits)
        assert verify._pattern_findings(value.lower(), set(), True)
    for value in ("Public ProductP01", "Server PostgreSQL13", "Odoo v17"):
        assert not matcher.find(value).hits
        assert not verify._pattern_findings(value.lower(), set(), True)


def test_hash_prefixed_support_ids_include_seven_spaced_digits():
    matcher = Matcher(Profile("Example", "v1", []), set())
    for value in ("Support #5478630", "Issue (#5 478 630)", "Ticket #5478630219"):
        assert any(hit.label == "ID" for hit in matcher.find(value).hits)
        assert verify._pattern_findings(value.lower(), set(), True)
    for value in ("color #112233", "Issue #123", "Amount 450 EUR", "Release 7.7.0"):
        assert not matcher.find(value).hits
        assert not verify._pattern_findings(value.lower(), set(), True)


def test_public_database_name_survives_partial_short_token_collision():
    # Fictional short profile tokens collide with mixed-case public software.
    terms = [Term("Sql", "CLIENT", case_sensitive=True), Term("Gre", "CLIENT", case_sensitive=True)]
    matcher = Matcher(Profile("Example", "v1", terms), set())
    rules = [verify._build_rule(t.text, True, False, []) for t in terms]
    for text in ("PostgreSql", "PostGres", "postgresql", "POSTGRES"):
        assert not matcher.find(text).hits
        assert not verify._term_findings(text, rules, any_case=True)
    assert matcher.find("SqlBank and Gre").hits
    assert verify._term_findings("SqlBank and Gre", rules, any_case=True)
    # A deliberate full software-name rule is still honored.
    full = Term("PostgreSQL", "PRODUCT")
    assert Matcher(Profile("Example", "v1", [full]), set()).find("PostgreSQL").hits
    assert verify._term_findings("PostgreSQL", [verify._build_rule(full.text, False, False, [])])
