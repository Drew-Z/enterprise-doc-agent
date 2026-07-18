from enterprise_doc_mcp.__main__ import build_parser


def test_mcp_package_exposes_cli_parser() -> None:
    parser = build_parser()

    assert parser.prog == "enterprise-doc-mcp"
