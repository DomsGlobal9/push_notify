import re

from rest_framework import serializers

TOPIC_RE = re.compile(r"^[A-Za-z0-9_.~%-]{1,100}$")
# Ids travel in URL paths (/v1/devices/<id>, /v1/users/<id>), so no "/" and no whitespace.
INSTALLATION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
USER_ID_RE = re.compile(r"^[^/\s?#%]{1,255}$")


def validate_installation_id(value):
    if not INSTALLATION_ID_RE.match(value):
        raise serializers.ValidationError("8-128 characters: letters, digits and . _ : -")
    return value


def validate_user_id(value):
    if value and not USER_ID_RE.match(value):
        raise serializers.ValidationError("User ids cannot contain '/', '?', '#', '%' or whitespace.")
    return value


def validate_topics(topics):
    bad = [t for t in topics if not TOPIC_RE.match(t)]
    if bad:
        raise serializers.ValidationError(
            f"Invalid topic names {bad[:5]}. Use letters, digits and _ . ~ % - (max 100 chars)."
        )
    return list(dict.fromkeys(topics))
