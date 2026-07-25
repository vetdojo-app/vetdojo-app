from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect, render
from .forms import SignUpForm

def signup(request):
    if request.user.is_authenticated:
        return redirect("qa:dashboard")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome — your account is ready.")
        return redirect("qa:dashboard")
    return render(request, "accounts/signup.html", {"form": form})
