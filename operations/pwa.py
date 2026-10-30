import json

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string

from .appearance import site_appearance


def manifest(request):
    colors = site_appearance(request)["site_palette"]
    return JsonResponse(
        {
            "name": "NexusLoan",
            "short_name": "NexusLoan",
            "start_url": "/operations/",
            "scope": "/",
            "display": "standalone",
            "background_color": colors["background"],
            "theme_color": colors["primary"],
            "icons": [
                {
                    "src": "/static/icons/loan-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                },
                {
                    "src": "/static/icons/loan-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                },
            ],
        },
        content_type="application/manifest+json",
    )


def worker(request):
    # Never cache authenticated HTML, statements, APIs, documents or payment responses.
    response = HttpResponse(
        "const OFFLINE_HTML = "
        + json.dumps(render_to_string("operations/offline.html", request=request))
        + ";\n"
        + """self.addEventListener('install', event => self.skipWaiting());
            self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
            self.addEventListener('fetch', event => {
            if (event.request.mode === 'navigate') {
                event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE_HTML, {
                status: 503,
                headers: {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'}
                })));
            }
            });""",
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response
