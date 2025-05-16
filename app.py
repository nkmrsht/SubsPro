from flask import Flask, request, jsonify, session, redirect, url_for, send_from_directory, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import os
import json
import uuid
import logging
import requests
import time
import io
import shutil
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()

# ロギングの設定
logging.basicConfig(level=logging.INFO)

# 永続データディレクトリの設定
PERSISTENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'persistent_data')
if not os.path.exists(PERSISTENT_DIR):
    os.makedirs(PERSISTENT_DIR)

# SQLiteデータベースファイルのパス
SQLITE_DB_PATH = os.path.join(PERSISTENT_DIR, 'subspro.db')

# アプリケーションの初期化
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-for-testing')

# SQLiteデータベース設定
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{SQLITE_DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
CORS(app, supports_credentials=True)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# データベースモデル
class User(UserMixin, db.Model):
    id = db.Column(db.String(36), primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))  # サイズを増やす
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    subscriptions = db.relationship('Subscription', backref='user', lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Subscription(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    fee = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='JPY', nullable=False)  # 'JPY' または 'USD'
    cycle = db.Column(db.String(20), nullable=False)  # 'monthly' または 'yearly'
    payment_day = db.Column(db.Integer, nullable=False)
    payment_month = db.Column(db.Integer, nullable=True)  # 年払いの場合に使用
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'fee': self.fee,
            'currency': self.currency,
            'cycle': self.cycle,
            'paymentDay': self.payment_day,
            'paymentMonth': self.payment_month,
        }

# ログインマネージャーのユーザーローダー
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

# 静的ファイルのルート
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# 追加：静的ファイルアクセス用ルート
@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)

# ユーザー登録API
@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    
    # ユーザー名が既に存在するか確認
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Username already exists'}), 400
    
    # 新しいユーザーを作成
    user = User(id=str(uuid.uuid4()), username=data['username'])
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    login_user(user)
    
    return jsonify({'success': True, 'user': {'id': user.id, 'username': user.username}}), 201

# ログインAPI
@app.route('/api/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        # GETリクエストの場合はHTMLページに戻る
        return redirect('/')
    
    data = request.get_json()
    
    user = User.query.filter_by(username=data['username']).first()
    
    if user and user.check_password(data['password']):
        login_user(user)
        return jsonify({'success': True, 'user': {'id': user.id, 'username': user.username}})
    
    return jsonify({'error': 'Invalid username or password'}), 401

# ログアウトAPI
@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({'success': True})

# 現在のユーザー情報取得API
@app.route('/api/user', methods=['GET'])
@login_required
def get_user():
    return jsonify({'id': current_user.id, 'username': current_user.username})

# 為替レート取得API
@app.route('/api/exchange-rate', methods=['GET'])
def get_exchange_rate():
    # キャッシュキー
    cache_key = 'exchange_rate_cache'
    cache_timeout = 3600  # 1時間キャッシュ
    
    # セッションからキャッシュを取得
    exchange_cache = session.get(cache_key)
    
    # キャッシュが有効かチェック
    if exchange_cache and time.time() < exchange_cache.get('expires_at', 0):
        return jsonify(exchange_cache['data'])
    
    try:
        # 為替レートAPIを呼び出す（無料）
        response = requests.get('https://open.er-api.com/v6/latest/USD')
        
        if response.status_code == 200:
            data = response.json()
            rate_usd_to_jpy = data['rates']['JPY']
            rate_jpy_to_usd = 1 / rate_usd_to_jpy
            
            result = {
                'USD_JPY': rate_usd_to_jpy,
                'JPY_USD': rate_jpy_to_usd,
                'timestamp': data['time_last_update_unix'],
                'date': data['time_last_update_utc']
            }
            
            # キャッシュに保存
            session[cache_key] = {
                'data': result,
                'expires_at': time.time() + cache_timeout
            }
            
            return jsonify(result)
        else:
            # APIが失敗した場合、デフォルト値を返す
            return jsonify({'error': 'Failed to fetch exchange rate', 'USD_JPY': 130, 'JPY_USD': 0.0077}), 500
    except Exception as e:
        logging.error(f"為替レート取得エラー: {e}")
        # エラーが発生した場合、デフォルト値を返す
        return jsonify({'error': str(e), 'USD_JPY': 130, 'JPY_USD': 0.0077}), 500

# サブスクリプション取得API
@app.route('/api/subscriptions', methods=['GET'])
@login_required
def get_subscriptions():
    subscriptions = [sub.to_dict() for sub in current_user.subscriptions]
    return jsonify(subscriptions)

# サブスクリプション作成API
@app.route('/api/subscriptions', methods=['POST'])
@login_required
def create_subscription():
    data = request.get_json()
    
    subscription = Subscription(
        id=str(uuid.uuid4()),
        name=data['name'],
        fee=data['fee'],
        currency=data.get('currency', 'JPY'),
        cycle=data['cycle'],
        payment_day=data['paymentDay'],
        payment_month=data.get('paymentMonth'),
        user_id=current_user.id
    )
    
    db.session.add(subscription)
    db.session.commit()
    
    return jsonify(subscription.to_dict()), 201

# サブスクリプション更新API
@app.route('/api/subscriptions/<subscription_id>', methods=['PUT'])
@login_required
def update_subscription(subscription_id):
    subscription = Subscription.query.filter_by(id=subscription_id, user_id=current_user.id).first()
    
    if not subscription:
        return jsonify({'error': 'Subscription not found'}), 404
    
    data = request.get_json()
    
    subscription.name = data['name']
    subscription.fee = data['fee']
    subscription.currency = data.get('currency', 'JPY')
    subscription.cycle = data['cycle']
    subscription.payment_day = data['paymentDay']
    subscription.payment_month = data.get('paymentMonth')
    
    db.session.commit()
    
    return jsonify(subscription.to_dict())

# サブスクリプション削除API
@app.route('/api/subscriptions/<subscription_id>', methods=['DELETE'])
@login_required
def delete_subscription(subscription_id):
    subscription = Subscription.query.filter_by(id=subscription_id, user_id=current_user.id).first()
    
    if not subscription:
        return jsonify({'error': 'Subscription not found'}), 404
    
    db.session.delete(subscription)
    db.session.commit()
    
    return jsonify({'success': True})

# データベース初期化
with app.app_context():
    # テーブルが存在しない場合のみ作成（既存データを保持）
    try:
        logging.info("データベーステーブルを確認しています...")
        db.create_all()
        logging.info("データベーステーブルの確認が完了しました")
    except Exception as e:
        logging.error(f"データベース初期化エラー: {e}")

# 管理者用API - 登録ユーザー一覧の取得（開発環境のみ）
@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    # 本番環境では無効化する仕組みを追加することを推奨
    users = User.query.all()
    user_list = [{
        'id': user.id,
        'username': user.username,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'subscription_count': len(user.subscriptions)
    } for user in users]
    return jsonify(user_list)

# データベースのバックアップ用API
@app.route('/api/admin/backup', methods=['GET'])
def backup_database():
    try:
        # データベースファイルのコピーを作成
        backup_file = os.path.join(PERSISTENT_DIR, f'subspro_backup_{int(time.time())}.db')
        shutil.copy2(SQLITE_DB_PATH, backup_file)
        
        # データエクスポート用のJSONデータを作成
        users = User.query.all()
        export_data = []
        
        for user in users:
            user_data = {
                'username': user.username,
                'password_hash': user.password_hash,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'subscriptions': []
            }
            
            for sub in user.subscriptions:
                sub_data = {
                    'name': sub.name,
                    'fee': sub.fee,
                    'currency': sub.currency,
                    'cycle': sub.cycle,
                    'payment_day': sub.payment_day,
                    'payment_month': sub.payment_month,
                    'created_at': sub.created_at.isoformat() if sub.created_at else None
                }
                user_data['subscriptions'].append(sub_data)
            
            export_data.append(user_data)
        
        # JSONファイルをメモリに作成
        json_data = json.dumps(export_data, indent=2, ensure_ascii=False)
        json_file = io.BytesIO(json_data.encode('utf-8'))
        
        # JSONファイルをダウンロード
        return send_file(
            json_file,
            mimetype='application/json',
            as_attachment=True,
            download_name=f'subspro_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        )
    except Exception as e:
        logging.error(f"バックアップエラー: {e}")
        return jsonify({'error': str(e)}), 500

# データベースの復元用API
@app.route('/api/admin/restore', methods=['POST'])
def restore_database():
    if 'backup_file' not in request.files:
        return jsonify({'error': 'バックアップファイルが見つかりません'}), 400
    
    file = request.files['backup_file']
    
    if file.filename == '':
        return jsonify({'error': 'ファイルが選択されていません'}), 400
    
    try:
        # JSONデータを解析
        json_data = json.loads(file.read().decode('utf-8'))
        
        # 既存のデータをクリア
        Subscription.query.delete()
        User.query.delete()
        db.session.commit()
        
        # ユーザーとサブスクリプションを復元
        for user_data in json_data:
            user = User(
                id=str(uuid.uuid4()),
                username=user_data['username'],
                password_hash=user_data['password_hash']
            )
            
            if user_data.get('created_at'):
                user.created_at = datetime.fromisoformat(user_data['created_at'])
            
            db.session.add(user)
            db.session.flush()  # IDを取得するためにフラッシュ
            
            for sub_data in user_data.get('subscriptions', []):
                subscription = Subscription(
                    id=str(uuid.uuid4()),
                    name=sub_data['name'],
                    fee=sub_data['fee'],
                    currency=sub_data.get('currency', 'JPY'),
                    cycle=sub_data['cycle'],
                    payment_day=sub_data['payment_day'],
                    payment_month=sub_data.get('payment_month'),
                    user_id=user.id
                )
                
                if sub_data.get('created_at'):
                    subscription.created_at = datetime.fromisoformat(sub_data['created_at'])
                
                db.session.add(subscription)
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'データベースが正常に復元されました'})
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"復元エラー: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)