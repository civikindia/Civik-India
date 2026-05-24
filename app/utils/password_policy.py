"""
Password Policy Enforcement for CivikIndia
Government-grade password requirements per NIC/CERT-In standards.
"""
import re

# Top 1000 most common passwords (abbreviated to top 200 for efficiency)
_COMMON_PASSWORDS = frozenset([
    'password', '123456', '12345678', 'qwerty', 'abc123', 'monkey', '1234567',
    'letmein', 'trustno1', 'dragon', 'baseball', 'iloveyou', 'master', 'sunshine',
    'ashley', 'michael', 'shadow', '123123', '654321', 'superman', 'qazwsx',
    'michael', 'football', 'password1', 'password123', 'batman', 'access',
    'hello', 'charlie', 'donald', '123456789', '1234567890', 'qwerty123',
    'password1234', 'admin', 'admin123', 'root', 'toor', 'pass', 'test',
    'guest', 'welcome', 'login', 'starwars', 'solo', 'passw0rd', 'master123',
    'flower', 'hottie', 'loveme', 'zaq1zaq1', 'iloveu', 'princess',
    'rockyou', 'nicole', 'daniel', 'babygirl', 'lovely', 'jessica',
    'jordan23', '654321', 'andrea', 'joshua', 'anthony', 'ashley1',
    'jessica1', 'jennifer', 'amanda', 'samantha', 'summer', 'abcdef',
    'abcdefg', '111111', '000000', 'soccer', 'hockey', 'charlie1',
    'Rangers', 'purple', 'george', 'hunter', 'maggie', 'jasmine',
    'andrew', 'harley', 'eagle1', 'mustang', 'robert', 'thomas',
    'qwert', 'asdfg', 'zxcvb', '1q2w3e', '1q2w3e4r', '1qaz2wsx',
    'buster', 'ginger', 'killer', 'pepper', 'banana', 'matrix',
    'whatever', 'computer', 'internet', 'server', 'database', 'security',
    'freedom', 'diamond', 'chicken', 'thunder', 'yankees', 'corvette',
    'mercedes', 'blahblah', 'cheese', 'cookie', 'coffee', 'dolphin',
    'tigger', 'sunshine1', 'trustn01', 'bonnie', 'dallas', 'ranger',
    'guitar', 'austin', 'taylor', 'phoenix', 'jordan', 'hannah',
    '123qwe', 'qwe123', 'zxcvbnm', 'asdfghjkl', 'qwertyuiop',
    'p@ssw0rd', 'p@ssword', 'Pa$$w0rd', 'password!', 'welcome1',
    'letmein1', 'monkey1', 'dragon1', 'master1', 'shadow1', 'matrix1',
    'changeme', 'passpass', 'secret', 'temp1234', 'India123', 'India@123',
    'admin@123', 'Admin@123', 'root@123', 'test@123', 'user@123',
    'welcome@123', 'password@1', 'password@123', 'P@ssw0rd', 'P@ssword1',
    'Abcd@1234', 'Qwerty@123', '12345@abcde', 'abcde@12345',
    'government', 'ministry', 'officer', 'delhi', 'mumbai', 'india',
    'bharat', 'hindustan', 'congress', 'republic',
])


def validate_password(password, username=None):
    """
    Validate password against government-grade policy.
    
    Requirements:
    - Minimum 12 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 digit
    - At least 1 special character (!@#$%^&*()_+-=[]{}|;:',.<>?/~`)
    - Not in common passwords list
    - Not containing the username
    
    Args:
        password: The password string to validate.
        username: Optional username to check against.
    
    Returns:
        tuple: (is_valid: bool, errors: list[str])
    """
    errors = []
    
    if not password:
        return False, ['Password is required.']
    
    if len(password) < 12:
        errors.append('Password must be at least 12 characters long.')
    
    if not re.search(r'[A-Z]', password):
        errors.append('Password must contain at least one uppercase letter.')
    
    if not re.search(r'[a-z]', password):
        errors.append('Password must contain at least one lowercase letter.')
    
    if not re.search(r'\d', password):
        errors.append('Password must contain at least one digit.')
    
    if not re.search(r'[!@#$%^&*()\-_+=\[\]{}|;:\'",.<>?/~`\\]', password):
        errors.append('Password must contain at least one special character.')
    
    if password.lower() in _COMMON_PASSWORDS:
        errors.append('This password is too common. Please choose a stronger one.')
    
    if username and username.lower() in password.lower():
        errors.append('Password must not contain your username.')
    
    return (len(errors) == 0), errors
