# list_gemini_models.py (robust version)
import os, sys, traceback
from dotenv import load_dotenv
load_dotenv()

# Make sure GEMINI_API_KEY or GOOGLE_API_KEY is set in the same shell
key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GENAI_API_KEY")
if not key:
    print("Warning: No GEMINI_API_KEY / GOOGLE_API_KEY / GENAI_API_KEY found in environment.")
    print("Set it in the same shell before running (temporary):")
    print(r'$env:GEMINI_API_KEY = "YOUR_KEY_HERE"  (PowerShell)')
    print()
# Try the modern google.genai client first
try:
    from google import genai
    print("Using google.genai client:", genai)
    try:
        client = genai.Client(api_key=key) if key else genai.Client()
    except Exception as e:
        print("Could not construct genai.Client with api_key:", e)
        # try with no args (may use ADC)
        try:
            client = genai.Client()
        except Exception as e2:
            print("Failed to construct genai.Client:", e2)
            client = None

    if client:
        printed = False
        # Try common variants for listing models:
        try:
            print("\nTrying: client.models.list()")
            models = client.models.list()   # no args variant
            for m in models:
                print("MODEL:", getattr(m, "name", getattr(m, "id", None) or repr(m)))
            printed = True
        except TypeError as e:
            print("client.models.list() TypeError:", e)
        except Exception as e:
            print("client.models.list() error:", e)
            traceback.print_exc()

        if not printed:
            try:
                print("\nTrying: client.list_models()")
                if hasattr(client, "list_models"):
                    models = client.list_models()
                    for m in models:
                        print("MODEL:", getattr(m, "name", getattr(m, "id", None) or repr(m)))
                    printed = True
                else:
                    print("client.list_models not present.")
            except Exception as e:
                print("client.list_models() error:", e)
                traceback.print_exc()

        # final attempt: try attribute access on client.models (maybe it's a container)
        if not printed:
            try:
                print("\nInspecting client.models (dir):")
                print(dir(client.models)[:50])
                # if client.models has an attribute 'get' or 'list_models', try them
                if hasattr(client.models, "get"):
                    print("client.models.get exists; attempting to iterate attribute names may not be possible.")
                # Try calling without arguments if callable
                if callable(client.models):
                    print("client.models is callable — attempting client.models()")
                    try:
                        models = client.models()
                        for m in models:
                            print("MODEL(called):", getattr(m, "name", getattr(m, "id", None) or repr(m)))
                        printed = True
                    except Exception as e:
                        print("client.models() call failed:", e)
                # If still nothing, try to access a sample attribute 'list' even if it errors (we handled above)
                if hasattr(client.models, "list"):
                    print("client.models.list exists; trying without kwargs again.")
                    try:
                        models = client.models.list()
                        for m in models:
                            print("MODEL:", getattr(m, "name", getattr(m, "id", None) or repr(m)))
                        printed = True
                    except Exception as e:
                        print("client.models.list() final attempt error:", e)
            except Exception as e:
                print("Inspection of client.models failed:", e)
                traceback.print_exc()

    else:
        print("genai.Client not created; skipping google.genai model listing.")
except Exception as e:
    print("google.genai import/usage failed:", e)
    traceback.print_exc()

# If that failed, try older 'genai' package patterns
try:
    import genai as legacy_genai
    print("\nUsing legacy 'genai' module:", legacy_genai)
    try:
        # Some older libs have genai.list_models or genai.Model.list() or genai.Models()
        if hasattr(legacy_genai, "list_models"):
            print("legacy_genai.list_models exists; calling it.")
            models = legacy_genai.list_models()
            for m in models:
                print("MODEL:", getattr(m, "name", getattr(m, "id", None) or repr(m)))
        elif hasattr(legacy_genai, "Models"):
            print("legacy_genai.Models exists; attempting Models.list()")
            try:
                models = legacy_genai.Models.list()
                for m in models:
                    print("MODEL:", getattr(m, "name", getattr(m, "id", None) or repr(m)))
            except Exception as e:
                print("Models.list failed:", e)
        else:
            print("legacy genai module doesn't expose list APIs I expected. dir(genai) sample:")
            print(dir(legacy_genai)[:80])
    except Exception as e:
        print("legacy genai listing attempt failed:", e)
        traceback.print_exc()
except Exception:
    print("\nNo legacy 'genai' module available or it failed to import. Done.")
