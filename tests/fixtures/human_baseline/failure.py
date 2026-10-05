"""A deliberate analysis error whose original record must survive repair."""

import aelix_analytics as analytics

sales = analytics.read("sales")
raise ValueError("Fixture: revenue requires source correction")
