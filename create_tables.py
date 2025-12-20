import pymysql
import os

def create():
    try:
        # Connection details - Ye values wahi hain jo tune Azure pe set ki hain
        conn = pymysql.connect(
            host="nyayconnect.mysql.database.azure.com",
            user="ujjwal",
            password="Rinku78@",  # <--- APNA PASSWORD YAHAN LIKH
            database="lawyer_app_db",
            ssl_verify_cert=True
        )
        
        with conn.cursor() as cursor:
            print("Connecting to database...")
            
            # 1. Users Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS Users (
                UserID INT AUTO_INCREMENT PRIMARY KEY,
                Name VARCHAR(255) NOT NULL,
                Email VARCHAR(255) UNIQUE NOT NULL,
                Password VARCHAR(255) NOT NULL,
                Role ENUM('Client', 'Lawyer') NOT NULL
            );
            """)
            
            # 2. Lawyer Profiles Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS LawyerProfiles (
                ProfileID INT AUTO_INCREMENT PRIMARY KEY,
                UserID INT UNIQUE,
                Bio TEXT,
                Specializations VARCHAR(255),
                Experience VARCHAR(100),
                City VARCHAR(100),
                ConsultationFee DECIMAL(10, 2),
                FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
            );
            """)
            
            # 3. Appointments Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS Appointments (
                AppointmentID INT AUTO_INCREMENT PRIMARY KEY,
                ClientID INT,
                LawyerID INT,
                AppointmentDate DATETIME NOT NULL,
                Notes TEXT,
                Status ENUM('Pending', 'Confirmed', 'Cancelled', 'Completed') DEFAULT 'Pending',
                FOREIGN KEY (ClientID) REFERENCES Users(UserID),
                FOREIGN KEY (LawyerID) REFERENCES Users(UserID)
            );
            """)
            
            conn.commit()
            print("✅ Mubarak ho bhai! Saari tables successfully ban gayi hain.")
            
    except Exception as e:
        print(f"❌ Error aaya hai bhai: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    create()