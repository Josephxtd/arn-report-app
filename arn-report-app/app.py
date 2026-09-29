import os
import json
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
import uuid

# Load ANTHROPIC_API_KEY (and anything else) from a local .env file.
# The file is git-ignored — the key never goes near the repo.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except ImportError:
    pass  # python-dotenv optional; a real env var works just as well

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
# Absolute paths so the app works regardless of the working directory it's launched from.
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(BASE_DIR, 'outputs')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

for _d in (app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER']):
    os.makedirs(_d, exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf'}


@app.before_request
def require_password():
    """
    Shared-password gate, on only when APP_PASSWORD is set (e.g. on Railway).
    The browser shows its own login prompt; any username works.
    """
    expected = os.environ.get('APP_PASSWORD')
    if not expected:
        return None
    auth = request.authorization
    import hmac
    if auth and auth.password and hmac.compare_digest(auth.password, expected):
        return None
    from flask import Response
    return Response(
        'Password required.', 401,
        {'WWW-Authenticate': 'Basic realm="ARN Report Generator"'},
    )

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    return render_template('index.html', ai_ready=_ai_ready())


def _ai_ready() -> bool:
    """True when a credential is present. Never returns or logs the key itself."""
    key = os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN')
    return bool(key and key.strip())


@app.route('/status')
def status():
    """Config check. Reports only whether a key is present, never its value."""
    key = os.environ.get('ANTHROPIC_API_KEY', '')
    return jsonify({
        'ai_ready': _ai_ready(),
        # Last 4 characters only — enough to tell two keys apart, useless if leaked.
        'key_hint': f'…{key[-4:]}' if len(key) > 8 else None,
        'model': 'claude-opus-5',
    })


@app.route('/generate', methods=['POST'])
def generate():
    if 'pdf_file' not in request.files:
        return jsonify({'error': 'No PDF file uploaded'}), 400

    file = request.files['pdf_file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file — please upload a PDF'}), 400

    # Save uploaded PDF
    uid = str(uuid.uuid4())[:8]
    filename = secure_filename(file.filename)
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{uid}_{filename}')
    file.save(upload_path)

    # Optional context the post log cannot contain — used only to steer the
    # written sections, never the figures.
    context = {
        k: request.form.get(k, '').strip()
        for k in ('industry', 'region', 'target_audience', 'notes')
    }

    try:
        from report_generator import generate_report
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{uid}_report.pdf')
        meta = generate_report(upload_path, output_path, context=context)

        safe_client = ''.join(
            c for c in meta.get('client_name', 'Client') if c.isalnum() or c in ' -_'
        ).strip().replace(' ', '_') or 'Client'

        resp = send_file(
            output_path,
            as_attachment=True,
            download_name=f"Post_Campaign_Report_{safe_client}.pdf",
            mimetype='application/pdf'
        )
        # Surface what was detected so the UI can show it after download.
        resp.headers['X-Detected-Client']   = meta.get('client_name', '')
        resp.headers['X-Detected-Contract'] = meta.get('contract_number', '')
        resp.headers['X-Detected-Stations'] = meta.get('stations', '')
        resp.headers['X-Detected-Spots']    = str(meta.get('total_spots', 0))
        resp.headers['X-Narrative']         = 'claude' if meta.get('ai_narrative') else 'template'
        resp.headers['X-Narrative-Cost']    = f"{meta.get('ai_cost', 0.0):.4f}"
        resp.headers['Access-Control-Expose-Headers'] = (
            'X-Detected-Client, X-Detected-Contract, X-Detected-Stations, '
            'X-Detected-Spots, X-Narrative, X-Narrative-Cost'
        )
        return resp
    except Exception as e:
        # Full traceback goes to the server log, not to the browser.
        app.logger.exception('Report generation failed')
        return jsonify({'error': str(e)}), 500
    finally:
        # The client's post log isn't needed once the report is built.
        try:
            os.remove(upload_path)
        except OSError:
            pass


if __name__ == '__main__':
    # PORT is supplied by the preview harness and by hosts like Railway.
    app.run(debug=False, host='127.0.0.1', port=int(os.environ.get('PORT', 5050)))
