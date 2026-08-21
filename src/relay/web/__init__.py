"""The demo console ("Delivery Theater"): Jinja2 templates + static assets served by
this same FastAPI app, no SPA build step -- see docs/adr/0006-phase-4-demo-console.md
for why. relay.web only ever renders HTML/serves static files; every action the console
takes is a plain fetch()/EventSource call against the real /v1 API (including
/v1/sandbox), so the API contract stays the one thing driving both a script and a human.
"""
