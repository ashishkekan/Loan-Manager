"""Authentication views: login and registration."""

from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.auth.views import LoginView
from django.contrib.messages import success
from .forms import RegistrationForm


class CustomLoginView(LoginView):
    """Styled login view with custom template."""
    template_name = 'accounts/login.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add placeholder styling via widget attrs
        context['form'].fields['username'].widget.attrs.update({
            'class': 'form-input', 'placeholder': 'Username'
        })
        context['form'].fields['password'].widget.attrs.update({
            'class': 'form-input', 'placeholder': 'Password'
        })
        return context


def register_view(request):
    """Handle user registration."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password1')
            user = authenticate(username=username, password=password)
            login(request, user)
            success(request, f'Welcome to LoanManager, {user.first_name}!')
            return redirect('dashboard')
    else:
        form = RegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})
