"""A deliberate analysis error whose original record must survive repair."""

import labkit as lab

sales = lab.read("sales")
raise ValueError("Fixture: revenue requires source correction")
