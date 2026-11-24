"""Print compact answers to the enterprise sample's five analytical questions."""
if __package__:
    from .generate_5_qa_report import print_report
else:
    from generate_5_qa_report import print_report


if __name__ == "__main__":
    print_report(compact=True)
