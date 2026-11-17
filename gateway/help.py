"""The Help center inside the dashboard (/admin/help/).

One guide page per sidebar item plus the cross-cutting guides. Pages are plain
templates in templates/admin/help/topics/<slug>.html; this module only lists
them, renders them inside the admin layout and links them from the sidebar
and from a "How this page works" button on every list page.
"""

from dataclasses import dataclass

from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from unfold.decorators import action


@dataclass(frozen=True)
class Topic:
    slug: str
    title: str
    icon: str
    summary: str
    group: str


TOPICS = [
    Topic("getting-started", "Getting started", "rocket_launch",
          "What the gateway does and the 6 steps from zero to a push on a phone.", "Start here"),
    Topic("add-new-app", "Add a new app, A to Z", "add_circle",
          "Every step for a new app: Firebase, Apple, product, apps, keys, app, backend, test, launch.", "Start here"),
    Topic("keys", "Where every key comes from", "vpn_key",
          "Each key and file: where to get it, where it goes, who needs it.", "Start here"),
    Topic("overview", "Overview", "space_dashboard",
          "What every card, the chart and the two tables on the home page mean.", "Monitor"),
    Topic("notifications", "Notifications", "send",
          "Every send, its status and counts, failures, and sending a test.", "Monitor"),
    Topic("devices", "Devices", "smartphone",
          "Phones and tablets that can receive pushes, and why one stops.", "Monitor"),
    Topic("queue", "Queue", "pending_actions",
          "The background work behind every send, and what to do when it is stuck.", "Monitor"),
    Topic("products", "Products", "apps",
          "One product per app family, its three keys, and rotating them.", "Setup"),
    Topic("apps", "Apps", "install_mobile",
          "The Android package and iOS bundle a product delivers to.", "Setup"),
    Topic("credentials", "Credentials", "key",
          "Firebase and Apple keys: how to create them and add them here.", "Setup"),
    Topic("admin-users", "Admin users", "manage_accounts",
          "Who can sign in to this dashboard, and passwords.", "Setup"),
    Topic("sending-api", "Sending from your backend", "code",
          "The API your servers call: targets, message fields, answers.", "Developers"),
    Topic("app-integration", "Connecting a Flutter app", "phone_iphone",
          "Adding push to a new app with the push_gateway_client package.", "Developers"),
    Topic("security", "Keys and security", "shield_lock",
          "Which secret lives where, what never goes in an app, rotation.", "Developers"),
    Topic("troubleshooting", "Troubleshooting", "build",
          "A push did not arrive: every failure reason and its fix.", "Developers"),
]
BY_SLUG = {t.slug: t for t in TOPICS}

# Which guide each admin list page links to.
MODEL_TOPICS = {
    "notifications_app.notification": "notifications",
    "devices_app.device": "devices",
    "dispatch_app.job": "queue",
    "tenants_app.tenant": "products",
    "tenants_app.app": "apps",
    "tenants_app.credential": "credentials",
    "auth.user": "admin-users",
}


class HelpButtonMixin:
    """Adds a "How this page works" button to a list page, opening its guide.

    Mix into an unfold ModelAdmin, set ``help_topic`` and list "how_it_works"
    in ``actions_list``.
    """

    help_topic: str = "getting-started"

    @action(description="How this page works", icon="help", url_path="how-it-works")
    def how_it_works(self, request):
        return redirect(reverse("admin:help-topic", args=[self.help_topic]))


def _groups():
    groups: dict[str, list[Topic]] = {}
    for t in TOPICS:
        groups.setdefault(t.group, []).append(t)
    return groups


def help_index(site, request):
    return TemplateResponse(request, "admin/help/index.html", {
        **site.each_context(request), "title": "Help", "groups": _groups(), "topics": TOPICS,
    })


def help_topic(site, request, slug):
    topic = BY_SLUG.get(slug)
    if topic is None:
        raise Http404("No such help page")
    i = TOPICS.index(topic)
    return TemplateResponse(request, "admin/help/topic.html", {
        **site.each_context(request),
        "title": f"Help: {topic.title}",
        "topic": topic,
        "groups": _groups(),
        "content_template": f"admin/help/topics/{slug}.html",
        "previous": TOPICS[i - 1] if i > 0 else None,
        "next": TOPICS[i + 1] if i + 1 < len(TOPICS) else None,
    })
