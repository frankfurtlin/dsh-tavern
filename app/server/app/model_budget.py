"""Account quotas; beta unlimited mode keeps usage reservations measurable."""
from .config import settings


def unlimited_usage():
    return settings.product_unlimited_usage


def call_budget_exceeded(profile, additional_calls):
    return not unlimited_usage() and profile.call_budget != 0 and profile.calls_reserved + additional_calls > profile.call_budget


def input_budget_exceeded(profile, additional):
    return not unlimited_usage() and profile.input_reserved + additional > profile.input_budget


def output_budget_exceeded(profile, additional):
    return not unlimited_usage() and profile.output_reserved + additional > profile.output_budget


def image_budget_exceeded(profile, count):
    return not unlimited_usage() and profile.daily_limit > 0 and count >= profile.daily_limit


def remaining_calls(profile, maximum):
    if unlimited_usage() or profile.call_budget == 0:
        return maximum
    return max(0, min(maximum, profile.call_budget - profile.calls_reserved))
