from flask import Flask, request, jsonify, session, redirect, url_for, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import os
import json
import uuid
import logging
from datetime import datetime
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()

# ロギングの設定
logging.basicConfig(level=logging.INFO)

# アプリケーションの初期化
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-for-testing')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
CORS(app, supports_credentials=True)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# データベースモデル
class User(UserMixin, db.Model):
    id = db.Column(db.String(36), primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
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
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)