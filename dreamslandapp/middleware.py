from django.shortcuts import redirect

class AgentAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        protected_paths = ['/properties/', '/dashboard/']  # Add paths that require login only
        if request.path in protected_paths:
            if not request.session.get('username'):
                return redirect('agent_login')
        return self.get_response(request)
