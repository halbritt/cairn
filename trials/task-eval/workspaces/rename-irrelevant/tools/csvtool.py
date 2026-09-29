import csv


def parse_row(row):
    return {"name": row[0], "qty": int(row[1])}


def load(path):
    with open(path) as f:
        return [parse_row(r) for r in csv.reader(f)]
