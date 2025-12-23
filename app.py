from flask import Flask, jsonify, request
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room  # Chat ke liye naya
import pymysql.cursors
import sys
import os
import jwt
import datetime
from dbutils.pooled_db import PooledDB
from functools import wraps
import json
from decimal import Decimal
from dateutil import parser

def get_db_connection():
    if pool is None:
        raise Exception("Database connect nahi hua hai!")
    return pool.connection()
 
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

# --- App Setup ---
app = Flask(__name__)
app.json_encoder = CustomJSONEncoder
bcrypt = Bcrypt(app)

# Socket.io Initialize (Isse real-time chat chalegi)
socketio = SocketIO(app, cors_allowed_origins="*")

# Final CORS Fix
CORS(app, resources={
    r"/*": {
        "origins": [
            "https://nyayconnect.me",
            "https://www.nyayconnect.me",
            "https://lawyer-website-iota.vercel.app",
            "http://localhost:3000"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
}, supports_credentials=True)

app.config['SECRET_KEY'] = 'this_is_a_very_secret_key'

# ==================== SIMPLE TEST ROUTES ====================

@app.route('/test', methods=['GET'])
def test_route():
    return jsonify({"message": "✅ Hello from your Python backend!"})

@app.route('/')
def home():
    return jsonify({"message": "🚀 Backend is running!"})

# ==================== DATABASE POOL START ====================
pool = None
try:
    db_host_value = os.environ.get('DB_HOST')
    
    if db_host_value and db_host_value.startswith('/cloudsql/'):
        conn_params = {
            'unix_socket': db_host_value,
            'user': os.environ.get('DB_USER'),
            'password': os.environ.get('DB_PASSWORD'),
            'database': os.environ.get('DB_NAME'),
            'cursorclass': pymysql.cursors.DictCursor, 
            'charset': 'utf8mb4'
        }
    else:
        conn_params = {
            'host': db_host_value or '127.0.0.1',
            'port': int(os.environ.get('DB_PORT', 8889)),
            'user': os.environ.get('DB_USER', 'root'),
            'password': os.environ.get('DB_PASSWORD', 'root'),
            'database': os.environ.get('DB_NAME', 'lawyer_app_db'),
            'cursorclass': pymysql.cursors.DictCursor, 
            'charset': 'utf8mb4'
        }

    pool = PooledDB(
        creator=pymysql, 
        maxconnections=10, 
        blocking=True,
        **conn_params
    )
    print("✅ Database connection pool created successfully.")
except Exception as e:
    print(f"⚠️ Database fail: {e}")
    pool = None 

# --- Authentication Decorator ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            token = request.headers['Authorization'].split(" ")[1]
        if not token: return jsonify({'message': 'Token is missing!'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            kwargs['current_user_id'] = data['UserID']
            kwargs['current_user_role'] = data['Role']
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return jsonify({'message': 'Token is invalid or has expired!'}), 401
        return f(*args, **kwargs)
    return decorated

# ==================== SOCKET.IO CHAT EVENTS ====================

# ==================== LIVE CHAT LOGIC ====================
@socketio.on('join_room')
def handle_join(data):
    room = f"room_{data['room_id']}"
    join_room(room)

@socketio.on('send_message')
def handle_send_message(data):
    room_id = data.get('room_id')
    sender_id = data.get('sender_id')
    message_text = data.get('message')
    room = f"room_{room_id}"

    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            # 1. Messages table mein save karo
            sql_msg = "INSERT INTO Messages (RoomID, SenderID, MessageText) VALUES (%s, %s, %s)"
            cursor.execute(sql_msg, (room_id, sender_id, message_text))
            
            # 2. ChatRooms table mein last message update karo
            sql_room = "UPDATE ChatRooms SET LastMessage = %s, LastMessageTime = NOW() WHERE RoomID = %s"
            cursor.execute(sql_room, (message_text, room_id))
            
            # 3. Commit karna bahut zaroori hai refresh fix karne ke liye
            connection.commit()
            print(f"✅ Success: Message saved for room {room_id}")

        # ✅ Database mein save hone ke baad hi sabko bhejo
        emit('receive_message', data, room=room)

    except Exception as e:
        print(f"❌ DATABASE ERROR: {str(e)}")
        if connection:
            connection.rollback()
    finally:
        if connection:
            connection.close()
@app.route('/api/chat/get_or_create_room', methods=['POST'])
@token_required
def get_or_create_room(current_user_id, current_user_role):
    data = request.get_json()
    lawyer_id = data.get('lawyerId')
    connection = pool.connection()
    try:
        with connection.cursor() as cursor:
            # Check room exists
            cursor.execute("SELECT RoomID FROM ChatRooms WHERE ClientID=%s AND LawyerID=%s", (current_user_id, lawyer_id))
            room = cursor.fetchone()
            if not room:
                # Create new room
                cursor.execute("INSERT INTO ChatRooms (ClientID, LawyerID) VALUES (%s, %s)", (current_user_id, lawyer_id))
                connection.commit()
                room_id = cursor.lastrowid
            else:
                room_id = room['RoomID']
        return jsonify({"success": True, "room_id": room_id})
    finally:
        connection.close()
# ==================== CHAT API ROUTES ====================

@app.route('/api/chat/rooms', methods=['GET'])
@token_required
def get_chat_rooms(current_user_id, current_user_role):
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            if current_user_role == 'Lawyer':
                query = """
                SELECT cr.RoomID, u.Name as ClientName, cr.LastMessage, cr.LastMessageTime 
                FROM ChatRooms cr JOIN Users u ON cr.ClientID = u.UserID 
                WHERE cr.LawyerID = %s ORDER BY cr.LastMessageTime DESC
                """
            else:
                query = """
                SELECT cr.RoomID, u.Name as LawyerName, cr.LastMessage, cr.LastMessageTime 
                FROM ChatRooms cr JOIN Users u ON cr.LawyerID = u.UserID 
                WHERE cr.ClientID = %s ORDER BY cr.LastMessageTime DESC
                """
            cursor.execute(query, (current_user_id,))
            rooms = cursor.fetchall()
        return jsonify({"success": True, "rooms": rooms})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/chat/messages/<int:room_id>', methods=['GET'])
@token_required
def get_messages(current_user_id, current_user_role, room_id):
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM Messages WHERE RoomID = %s ORDER BY Timestamp ASC", (room_id,))
            messages = cursor.fetchall()
        return jsonify({"success": True, "messages": messages})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/chat/send', methods=['POST'])
@token_required
def save_chat_message(current_user_id, current_user_role):
    connection = None
    try:
        data = request.get_json()
        room_id, msg_text = data.get('room_id'), data.get('message')
        connection = pool.connection()
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO Messages (RoomID, SenderID, MessageText) VALUES (%s, %s, %s)", 
                           (room_id, current_user_id, msg_text))
            cursor.execute("UPDATE ChatRooms SET LastMessage = %s, LastMessageTime = NOW() WHERE RoomID = %s", 
                           (msg_text, room_id))
        connection.commit()
        return jsonify({"success": True})
    except Exception as e:
        if connection: connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()
        
          

# ==================== USER & AUTH ROUTES ====================

@app.route('/api/register', methods=['POST'])
def register_user():
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            data = request.get_json()
            name, email, password, role = data.get('name'), data.get('email'), data.get('password'), data.get('role')
            if not all([name, email, password, role]): return jsonify({"success": False, "message": "All fields are required."}), 400
            
            cursor.execute("SELECT UserID FROM Users WHERE Email = %s", (email,))
            if cursor.fetchone():
                return jsonify({"success": False, "message": "Email already registered."}), 409

            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            query = "INSERT INTO Users (Name, Email, Password, Role) VALUES (%s, %s, %s, %s)"
            cursor.execute(query, (name, email, hashed_password, role))
            new_user_id = cursor.lastrowid
        connection.commit()
        return jsonify({"success": True, "message": "User registered successfully!", "userId": new_user_id}), 201
    except Exception as e:
        if connection: connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/login', methods=['POST'])
def login_user():
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            data = request.get_json()
            email, password = data.get('email'), data.get('password')
            if not email or not password: return jsonify({"success": False, "message": "Email and password are required."}), 400
            
            clean_email = email.strip()
            cursor.execute("SELECT * FROM Users WHERE Email = %s", (clean_email,))
            user = cursor.fetchone()
            
        if not user or not bcrypt.check_password_hash(user['Password'], password): 
            return jsonify({"success": False, "message": "Invalid credentials."}), 401
        
        payload = {
            'UserID': user['UserID'],
            'Role': user['Role'],
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }
        
        token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({"success": True, "message": "Login successful!", "token": token, "role": user['Role'], "userId": user['UserID']})
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ==================== USER PROFILE ROUTES ====================

@app.route('/api/user/profile', methods=['GET'])
@token_required
def get_user_profile(current_user_id, current_user_role):
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT UserID, Name, Email, Role FROM Users WHERE UserID = %s", (current_user_id,))
            user = cursor.fetchone()
            if not user: return jsonify({"success": False, "message": "User not found"}), 404
            user_data = {
                "name": user['Name'], "email": user['Email'], "role": user['Role'],
                "joinDate": 'January 2024', "userId": user['UserID']
            }
        return jsonify({"success": True, "user": user_data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/user/profile', methods=['PUT'])
@token_required
def update_user_profile(current_user_id, current_user_role):
    connection = None
    try:
        data = request.get_json()
        name, phone, address = data.get('name'), data.get('phone'), data.get('address')
        if not name: return jsonify({"success": False, "message": "Name is required"}), 400
        connection = pool.connection()
        with connection.cursor() as cursor:
            cursor.execute("UPDATE Users SET Name = %s WHERE UserID = %s", (name, current_user_id))
            try:
                cursor.execute("SELECT * FROM UserProfiles WHERE UserID = %s", (current_user_id,))
                if cursor.fetchone():
                    cursor.execute("UPDATE UserProfiles SET Phone = %s, Address = %s WHERE UserID = %s", (phone, address, current_user_id))
            except: pass
        connection.commit()
        return jsonify({"success": True, "message": "Profile updated successfully"})
    except Exception as e:
        if connection: connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ==================== LAWYER SPECIFIC ROUTES ====================

@app.route('/api/lawyer/appointments', methods=['GET'])
@token_required
def get_lawyer_appointments(current_user_id, current_user_role):
    if current_user_role != 'Lawyer': return jsonify({"success": False, "message": "Access forbidden."}), 403
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            query = """
            SELECT a.AppointmentID, a.AppointmentDate, a.Status, a.Notes, 
                   u.Name AS ClientName, u.Email AS ClientEmail, lp.ConsultationFee
            FROM Appointments a 
            JOIN Users u ON a.ClientID = u.UserID 
            LEFT JOIN LawyerProfiles lp ON a.LawyerID = lp.UserID
            WHERE a.LawyerID = %s ORDER BY a.AppointmentDate DESC
            """
            cursor.execute(query, (current_user_id,))
            appointments = cursor.fetchall()
        return jsonify({"success": True, "appointments": appointments})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/my-lawyer-profile', methods=['GET', 'POST'])
@token_required
def my_lawyer_profile_handler(current_user_id, current_user_role):
    if current_user_role != 'Lawyer':
        return jsonify({"success": False, "message": "Access forbidden."}), 403
    
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            if request.method == 'GET':
                query = """
                SELECT u.UserID, u.Name, u.Email, u.Role as UserType,
                       lp.Bio, lp.Specializations, lp.Experience, 
                       lp.ConsultationFee, lp.City
                FROM Users u 
                LEFT JOIN LawyerProfiles lp ON u.UserID = lp.UserID 
                WHERE u.UserID = %s
                """
                cursor.execute(query, (current_user_id,))
                profile = cursor.fetchone()
                if not profile:
                    cursor.execute("SELECT UserID, Name, Email, Role as UserType FROM Users WHERE UserID = %s", (current_user_id,))
                    profile = cursor.fetchone()
                if not profile:
                    return jsonify({"success": False, "message": "User not found."}), 404
                profile['CreatedAt'] = datetime.datetime.now()
                return jsonify({"success": True, "profile": profile})

            elif request.method == 'POST':
                data = request.get_json()
                bio, specializations, experience, city, fee = data.get('bio'), data.get('specializations'), data.get('experience'), data.get('city'), data.get('consultationFee')
                cursor.execute("SELECT UserID FROM LawyerProfiles WHERE UserID = %s", (current_user_id,))
                if cursor.fetchone():
                    query = "UPDATE LawyerProfiles SET Bio=%s, Specializations=%s, Experience=%s, City=%s, ConsultationFee=%s WHERE UserID=%s"
                    params = (bio, specializations, experience, city, fee, current_user_id)
                else:
                    query = "INSERT INTO LawyerProfiles (UserID, Bio, Specializations, Experience, City, ConsultationFee) VALUES (%s, %s, %s, %s, %s, %s)"
                    params = (current_user_id, bio, specializations, experience, city, fee)
                cursor.execute(query, params)
                connection.commit()
                return jsonify({"success": True, "message": "Profile updated successfully!"}), 200
    except Exception as e:
        if connection: connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ==================== APPOINTMENT HISTORY ROUTES ====================

@app.route('/api/appointment-history', methods=['GET'])
@token_required
def get_appointment_history(current_user_id, current_user_role):
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            if current_user_role == 'Client':
                query = """
                SELECT a.AppointmentID as id, CONCAT('APT-', a.AppointmentID) as appointmentId, a.AppointmentDate as date,
                       'Consultation' as type, lp.ConsultationFee as fee, a.Status as status, u.Name as lawyerName,
                       lp.Specializations as specialization, '30 mins' as duration
                FROM Appointments a JOIN Users u ON a.LawyerID = u.UserID JOIN LawyerProfiles lp ON a.LawyerID = lp.UserID
                WHERE a.ClientID = %s ORDER BY a.AppointmentDate DESC
                """
            else:
                query = """
                SELECT a.AppointmentID as id, CONCAT('APT-', a.AppointmentID) as appointmentId, a.AppointmentDate as date,
                       'Legal Service' as type, lp.ConsultationFee as fee, a.Status as status, u.Name as clientName,
                       lp.Specializations as specialization, '45 mins' as duration
                FROM Appointments a JOIN Users u ON a.ClientID = u.UserID JOIN LawyerProfiles lp ON a.LawyerID = lp.UserID
                WHERE a.LawyerID = %s ORDER BY a.AppointmentDate DESC
                """
            cursor.execute(query, (current_user_id,))
            appointments = cursor.fetchall()
            formatted_appointments = []
            for appt in appointments:
                formatted_appointments.append({
                    "id": appt['appointmentId'], "date": appt['date'].strftime('%Y-%m-%d') if appt['date'] else '',
                    "fee": float(appt['fee']) if appt['fee'] else 0, "status": appt['status'], "type": appt['type'],
                    "duration": appt['duration'], "lawyerName": appt.get('lawyerName'), "clientName": appt.get('clientName'),
                    "specialization": appt.get('specialization', 'General Law')
                })
        return jsonify({"success": True, "appointments": formatted_appointments})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ==================== LAWYER & PROFILE ROUTES ====================

@app.route('/api/lawyers', methods=['GET'])
def get_lawyers():
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            query = "SELECT u.UserID, u.Name, lp.Specializations, lp.City, lp.ConsultationFee FROM Users u LEFT JOIN LawyerProfiles lp ON u.UserID = lp.UserID WHERE u.Role = 'Lawyer'"
            cursor.execute(query)
            lawyers = cursor.fetchall()
        return jsonify(lawyers)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/lawyers/<int:lawyer_id>', methods=['GET'])
def get_lawyer_profile(lawyer_id):
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            query = "SELECT u.UserID, u.Name, u.Email, lp.Bio, lp.Specializations, lp.Experience, lp.ConsultationFee, lp.City FROM Users u LEFT JOIN LawyerProfiles lp ON u.UserID = lp.UserID WHERE u.UserID = %s AND u.Role = 'Lawyer'"
            cursor.execute(query, (lawyer_id,))
            lawyer = cursor.fetchone()
        if lawyer: return jsonify(lawyer)
        else: return jsonify({"success": False, "message": "Lawyer not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/lawyer-profile', methods=['POST', 'PUT'])
@token_required
def create_or_update_lawyer_profile(current_user_id, current_user_role):
    if current_user_role != 'Lawyer': return jsonify({"success": False, "message": "Access forbidden."}), 403
    connection = None
    try:
        data = request.get_json()
        bio, specializations, experience, city, fee = data.get('bio'), data.get('specializations'), data.get('experience'), data.get('city'), data.get('consultationFee')
        if not all([bio, specializations, experience, city, fee]): return jsonify({"success": False, "message": "All profile fields are required."}), 400
        connection = pool.connection()
        with connection.cursor() as cursor:
            query = "INSERT INTO LawyerProfiles (UserID, Bio, Specializations, Experience, City, ConsultationFee) VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE Bio=%s, Specializations=%s, Experience=%s, City=%s, ConsultationFee=%s"
            params = (current_user_id, bio, specializations, experience, city, fee, bio, specializations, experience, city, fee)
            cursor.execute(query, params)
        connection.commit()
        return jsonify({"success": True, "message": "Profile updated successfully!"}), 201
    except Exception as e:
        if connection: connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ==================== APPOINTMENT ROUTES ====================

@app.route('/api/appointments', methods=['POST'])
@token_required
def book_appointment(current_user_id, current_user_role):
    if current_user_role != 'Client': return jsonify({"success": False, "message": "Only clients can book appointments."}), 403
    connection = None
    try:
        data = request.get_json()
        lawyer_id, appointment_date_iso, notes = data.get('lawyerId'), data.get('appointmentDate'), data.get('notes', '')
        if not lawyer_id or not appointment_date_iso: return jsonify({"success": False, "message": "Lawyer ID and appointment date are required."}), 400
        mysql_datetime_str = parser.isoparse(appointment_date_iso).strftime('%Y-%m-%d %H:%M:%S')
        connection = pool.connection()
        with connection.cursor() as cursor:
            query = "INSERT INTO Appointments (ClientID, LawyerID, AppointmentDate, Notes, Status) VALUES (%s, %s, %s, %s, %s)"
            cursor.execute(query, (current_user_id, lawyer_id, mysql_datetime_str, notes, 'Pending'))
        connection.commit()
        return jsonify({"success": True, "message": "Appointment booked successfully."}), 201
    except Exception as e:
        if connection: connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/appointments/<int:appointment_id>', methods=['PUT'])
@token_required
def update_appointment_status(appointment_id, current_user_id, current_user_role):
    if current_user_role != 'Lawyer': return jsonify({"success": False, "message": "Only lawyers can update appointment status."}), 403
    connection = None
    try:
        data = request.get_json()
        new_status = data.get('status')
        if not new_status or new_status not in ['Confirmed', 'Cancelled', 'Completed']: return jsonify({"success": False, "message": "Invalid status provided."}), 400
        connection = pool.connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT AppointmentID FROM Appointments WHERE AppointmentID = %s AND LawyerID = %s", (appointment_id, current_user_id))
            if not cursor.fetchone(): return jsonify({"success": False, "message": "Appointment not found or you don't have permission."}), 404
            cursor.execute("UPDATE Appointments SET Status = %s WHERE AppointmentID = %s", (new_status, appointment_id))
        connection.commit()
        return jsonify({"success": True, "message": "Appointment status updated."}), 200
    except Exception as e:
        if connection: connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

@app.route('/api/my-appointments', methods=['GET'])
@token_required
def get_my_appointments(current_user_id, current_user_role):
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            if current_user_role == 'Client':
                query = """
                SELECT a.AppointmentID, a.AppointmentDate, a.Status, a.Notes, u.Name AS LawyerName,
                       lp.ConsultationFee, lp.Specializations
                FROM Appointments a JOIN Users u ON a.LawyerID = u.UserID LEFT JOIN LawyerProfiles lp ON a.LawyerID = lp.UserID
                WHERE a.ClientID = %s ORDER BY a.AppointmentDate DESC
                """
            elif current_user_role == 'Lawyer':
                query = """
                SELECT a.AppointmentID, a.AppointmentDate, a.Status, a.Notes, u.Name AS ClientName, lp.ConsultationFee
                FROM Appointments a JOIN Users u ON a.ClientID = u.UserID LEFT JOIN LawyerProfiles lp ON a.LawyerID = lp.UserID
                WHERE a.LawyerID = %s ORDER BY a.AppointmentDate DESC
                """
            else: return jsonify({"success": False, "message": "Invalid user role."}), 400
            cursor.execute(query, (current_user_id,))
            appointments = cursor.fetchall()
        return jsonify(appointments)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

# ==================== DASHBOARD STATS ====================

@app.route('/api/dashboard/stats', methods=['GET'])
@token_required
def get_dashboard_stats(current_user_id, current_user_role):
    connection = None
    try:
        connection = pool.connection()
        with connection.cursor() as cursor:
            if current_user_role == 'Lawyer':
                cursor.execute("SELECT COUNT(*) as total_appointments FROM Appointments WHERE LawyerID = %s", (current_user_id,))
                total_appointments = cursor.fetchone()['total_appointments']
                cursor.execute("SELECT COUNT(*) as pending_appointments FROM Appointments WHERE LawyerID = %s AND Status = 'Pending'", (current_user_id,))
                pending_appointments = cursor.fetchone()['pending_appointments']
                cursor.execute("SELECT COUNT(*) as completed_appointments FROM Appointments WHERE LawyerID = %s AND Status = 'Completed'", (current_user_id,))
                completed_appointments = cursor.fetchone()['completed_appointments']
                cursor.execute("SELECT AVG(ConsultationFee) as avg_earning FROM LawyerProfiles WHERE UserID = %s", (current_user_id,))
                avg_earning = cursor.fetchone()['avg_earning'] or 0
                stats = {
                    "totalAppointments": total_appointments, "pendingAppointments": pending_appointments,
                    "completedAppointments": completed_appointments, "averageEarning": float(avg_earning)
                }
            else:
                cursor.execute("SELECT COUNT(*) as total_appointments FROM Appointments WHERE ClientID = %s", (current_user_id,))
                total_appointments = cursor.fetchone()['total_appointments']
                cursor.execute("SELECT COUNT(*) as upcoming_appointments FROM Appointments WHERE ClientID = %s AND Status = 'Confirmed'", (current_user_id,))
                upcoming_appointments = cursor.fetchone()['upcoming_appointments']
                cursor.execute("SELECT COUNT(*) as completed_appointments FROM Appointments WHERE ClientID = %s AND Status = 'Completed'", (current_user_id,))
                completed_appointments = cursor.fetchone()['completed_appointments']
                stats = {
                    "totalConsultations": total_appointments, "upcomingConsultations": upcoming_appointments,
                    "completedConsultations": completed_appointments
                }
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if connection: connection.close()

# --- RUN THE APP ---
if __name__ == '__main__':
    # Real-time ke liye ab socketio.run use karenge
    socketio.run(app, debug=True, port=5001)