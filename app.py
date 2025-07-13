
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import openai
import json
import os
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = os.envrion.get("secret")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///learning_app.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Set OpenAI API key
openai.api_key = os.environ.get("openai")

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    topics = db.relationship('Topic', backref='user', lazy=True, cascade='all, delete-orphan')

class Topic(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    timeframe_days = db.Column(db.Integer, nullable=False)
    frequency = db.Column(db.String(20), nullable=False)  # daily, weekly, etc.
    conversation_history = db.Column(db.Text)  # JSON string of conversation
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    current_day = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    
    daily_content = db.relationship('DailyContent', backref='topic', lazy=True, cascade='all, delete-orphan')
    user_progress = db.relationship('UserProgress', backref='topic', lazy=True, cascade='all, delete-orphan')

class DailyContent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey('topic.id'), nullable=False)
    day_number = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text, nullable=False)  # JSON string of 3 notes
    flashcards = db.Column(db.Text, nullable=False)  # JSON string of flashcards
    today_quiz = db.Column(db.Text, nullable=False)  # JSON string of today's quiz
    overall_quiz = db.Column(db.Text, nullable=False)  # JSON string of overall quiz
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UserProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic_id = db.Column(db.Integer, db.ForeignKey('topic.id'), nullable=False)
    day_number = db.Column(db.Integer, nullable=False)
    content_type = db.Column(db.String(20), nullable=False)  # flashcard, today_quiz, overall_quiz
    question_id = db.Column(db.String(50), nullable=False)  # identifier for the specific question
    is_correct = db.Column(db.Boolean, nullable=True)  # None for flashcards, True/False for quizzes
    time_spent = db.Column(db.Integer, nullable=False)  # seconds
    attempted_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='progress_records', lazy=True)

# Helper functions
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None

def generate_content_with_gpt(prompt, model="gpt-4o-mini"):
    """Generate content using OpenAI API with caching consideration"""
    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are an expert educational content creator. Create engaging, clear, and pedagogically sound learning materials."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"OpenAI API error: {e}")
        return None

def analyze_user_weaknesses(user_id, topic_id):
    """Analyze user's weak areas based on progress data"""
    # Get all wrong answers and time spent data
    wrong_answers = UserProgress.query.filter_by(
        user_id=user_id, 
        topic_id=topic_id, 
        is_correct=False
    ).all()
    
    slow_responses = UserProgress.query.filter_by(
        user_id=user_id, 
        topic_id=topic_id
    ).filter(UserProgress.time_spent > 30).all()  # More than 30 seconds
    
    # Create weakness summary
    weakness_areas = []
    for record in wrong_answers:
        weakness_areas.append(f"Day {record.day_number} - {record.content_type} - Question {record.question_id}")
    
    return weakness_areas

def generate_daily_content(topic, day_number, conversation_history, weakness_areas=None):
    """Generate all content for a specific day"""
    
    # Build context from conversation and previous days
    context = f"""
    Topic: {topic.title}
    Description: {topic.description}
    Timeframe: {topic.timeframe_days} days
    Current Day: {day_number}
    Conversation History: {conversation_history}
    """
    
    if weakness_areas:
        context += f"\nUser's weak areas: {', '.join(weakness_areas)}"
    
    # Generate Notes
    notes_prompt = f"""
    {context}
    
    Create exactly 3 educational notes for day {day_number} of learning {topic.title}.
    
    Requirements:
    - Each note should build on previous concepts if this isn't day 1
    - Make them progressively more detailed
    - Include practical examples
    - Format as JSON with structure: {{"notes": [{{"title": "Note Title", "content": "Detailed explanation"}}]}}
    """
    
    notes_response = generate_content_with_gpt(notes_prompt)
    
    # Generate Flashcards
    flashcards_prompt = f"""
    {context}
    
    Based on the 3 notes for day {day_number}, create flashcards to reinforce learning.
    
    Requirements:
    - Create as many flashcards as needed to support the 3 notes (typically 5-8 cards)
    - Front: Question or term
    - Back: Clear explanation or answer
    - Format as JSON: {{"flashcards": [{{"front": "Question/Term", "back": "Answer/Explanation", "note_reference": "Which note this relates to"}}]}}
    """
    
    flashcards_response = generate_content_with_gpt(flashcards_prompt)
    
    # Generate Today's Quiz
    today_quiz_prompt = f"""
    {context}
    
    Create a 5-6 question quiz covering today's content (day {day_number}).
    
    Requirements:
    - Mix of multiple choice and short answer questions
    - Focus on key concepts from today's notes
    - Include correct answers and brief explanations
    - Format as JSON: {{"quiz": [{{"question": "Question text", "type": "multiple_choice", "options": ["A", "B", "C", "D"], "correct_answer": "A", "explanation": "Why this is correct"}}]}}
    """
    
    today_quiz_response = generate_content_with_gpt(today_quiz_prompt)
    
    # Generate Overall Quiz
    overall_quiz_prompt = f"""
    {context}
    
    Create an 8-10 question quiz covering all content learned so far (days 1-{day_number}).
    
    Requirements:
    - Review key concepts from all previous days
    - Include some new questions that test deeper understanding
    - Focus extra attention on weakness areas if provided
    - Mix of question types
    - Format as JSON: {{"quiz": [{{"question": "Question text", "type": "multiple_choice", "options": ["A", "B", "C", "D"], "correct_answer": "A", "explanation": "Why this is correct"}}]}}
    """
    
    overall_quiz_response = generate_content_with_gpt(overall_quiz_prompt)
    
    return {
        'notes': notes_response,
        'flashcards': flashcards_response,
        'today_quiz': today_quiz_response,
        'overall_quiz': overall_quiz_response
    }

# Routes
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists')
            return render_template('register.html')
        
        user = User(
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(user)
        db.session.commit()
        
        session['user_id'] = user.id
        return redirect(url_for('dashboard'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    topics = Topic.query.filter_by(user_id=user.id, is_active=True).all()
    return render_template('dashboard.html', user=user, topics=topics)

@app.route('/create_topic', methods=['GET', 'POST'])
@login_required
def create_topic():
    user = get_current_user()
    
    # Check topic limit
    active_topics = Topic.query.filter_by(user_id=user.id, is_active=True).count()
    if active_topics >= 5:
        flash('Maximum 5 topics allowed. Please archive a topic first.')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        timeframe_days = int(request.form['timeframe_days'])
        frequency = request.form['frequency']
        
        # Create topic
        topic = Topic(
            user_id=user.id,
            title=title,
            description=description,
            timeframe_days=timeframe_days,
            frequency=frequency
        )
        db.session.add(topic)
        db.session.commit()
        
        # Start conversation with LLM
        return redirect(url_for('topic_conversation', topic_id=topic.id))
    
    return render_template('create_topic.html')

@app.route('/topic_conversation/<int:topic_id>')
@login_required
def topic_conversation(topic_id):
    user = get_current_user()
    topic = Topic.query.filter_by(id=topic_id, user_id=user.id).first_or_404()
    
    # Start conversation if not already started
    if not topic.conversation_history:
        # Generate initial questions
        initial_prompt = f"""
        A user wants to learn about: {topic.title}
        Description: {topic.description}
        Timeframe: {topic.timeframe_days} days
        
        Ask 2-3 thoughtful questions to better understand:
        1. Their current knowledge level
        2. Their specific goals
        3. What they hope to achieve
        
        Keep it conversational and encouraging.
        """
        
        initial_response = generate_content_with_gpt(initial_prompt)
        conversation = [{"role": "assistant", "content": initial_response}]
        topic.conversation_history = json.dumps(conversation)
        db.session.commit()
    
    conversation = json.loads(topic.conversation_history)
    return render_template('topic_conversation.html', topic=topic, conversation=conversation)

@app.route('/topic_conversation/<int:topic_id>/respond', methods=['POST'])
@login_required
def respond_to_conversation(topic_id):
    user = get_current_user()
    topic = Topic.query.filter_by(id=topic_id, user_id=user.id).first_or_404()
    
    user_response = request.json['response']
    conversation = json.loads(topic.conversation_history)
    
    # Add user response
    conversation.append({"role": "user", "content": user_response})
    
    # Generate AI response
    if len(conversation) < 6:  # Continue conversation
        prompt = f"""
        Continue the conversation about learning {topic.title}.
        Previous conversation: {json.dumps(conversation)}
        
        Ask one more helpful question or provide encouragement, then if this is the 3rd exchange, 
        say you're ready to start generating their learning content.
        """
        
        ai_response = generate_content_with_gpt(prompt)
        conversation.append({"role": "assistant", "content": ai_response})
    else:
        # End conversation and generate content
        conversation.append({"role": "assistant", "content": "Perfect! I have everything I need. Let me generate your first two days of learning content..."})
        
        # Generate Day 1 and Day 2 content
        conversation_summary = " ".join([msg["content"] for msg in conversation if msg["role"] == "user"])
        
        for day in [1, 2]:
            content = generate_daily_content(topic, day, conversation_summary)
            
            daily_content = DailyContent(
                topic_id=topic.id,
                day_number=day,
                notes=content['notes'],
                flashcards=content['flashcards'],
                today_quiz=content['today_quiz'],
                overall_quiz=content['overall_quiz']
            )
            db.session.add(daily_content)
    
    topic.conversation_history = json.dumps(conversation)
    db.session.commit()
    
    return jsonify({"response": conversation[-1]["content"], "finished": len(conversation) >= 7})

@app.route('/learn/<int:topic_id>')
@login_required
def learn_next(topic_id):
    user = get_current_user()
    topic = Topic.query.filter_by(id=topic_id, user_id=user.id).first_or_404()
    
    # Redirect to paginated version
    return redirect(url_for('learn_paginated', topic_id=topic_id))

@app.route('/complete_day/<int:topic_id>')
@login_required
def complete_day(topic_id):
    user = get_current_user()
    topic = Topic.query.filter_by(id=topic_id, user_id=user.id).first_or_404()
    
    # Update current day
    topic.current_day += 1
    
    # Generate next day's content if needed
    next_day = topic.current_day + 1
    if next_day <= topic.timeframe_days:
        existing_content = DailyContent.query.filter_by(
            topic_id=topic.id, 
            day_number=next_day
        ).first()
        
        if not existing_content:
            # Analyze weaknesses
            weakness_areas = analyze_user_weaknesses(user.id, topic.id)
            conversation_summary = json.loads(topic.conversation_history) if topic.conversation_history else ""
            
            # Generate next day content
            content = generate_daily_content(topic, next_day, conversation_summary, weakness_areas)
            
            daily_content = DailyContent(
                topic_id=topic.id,
                day_number=next_day,
                notes=content['notes'],
                flashcards=content['flashcards'],
                today_quiz=content['today_quiz'],
                overall_quiz=content['overall_quiz']
            )
            db.session.add(daily_content)
    
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/track_progress', methods=['POST'])
@login_required
def track_progress():
    user = get_current_user()
    data = request.json
    
    progress = UserProgress(
        user_id=user.id,
        topic_id=data['topic_id'],
        day_number=data['day_number'],
        content_type=data['content_type'],
        question_id=data['question_id'],
        is_correct=data.get('is_correct'),
        time_spent=data['time_spent']
    )
    
    db.session.add(progress)
    db.session.commit()
    
    return jsonify({"status": "success"})

@app.route('/flashcards/<int:topic_id>')
@login_required
def view_all_flashcards(topic_id):
    user = get_current_user()
    topic = Topic.query.filter_by(id=topic_id, user_id=user.id).first_or_404()
    
    # Get all daily content up to current day
    all_content = DailyContent.query.filter_by(topic_id=topic.id).filter(
        DailyContent.day_number <= topic.current_day
    ).order_by(DailyContent.day_number).all()
    
    # Collect all flashcards
    all_flashcards = []
    for content in all_content:
        try:
            flashcards_data = json.loads(content.flashcards)
            if 'flashcards' in flashcards_data:
                for i, flashcard in enumerate(flashcards_data['flashcards']):
                    all_flashcards.append({
                        'day': content.day_number,
                        'id': f"day{content.day_number}_card{i+1}",
                        'front': flashcard['front'],
                        'back': flashcard['back'],
                        'note_reference': flashcard.get('note_reference', 'General')
                    })
        except (json.JSONDecodeError, KeyError):
            continue
    
    return render_template('all_flashcards.html', topic=topic, flashcards=all_flashcards)

@app.route('/learn_paginated/<int:topic_id>')
@login_required
def learn_paginated(topic_id):
    user = get_current_user()
    topic = Topic.query.filter_by(id=topic_id, user_id=user.id).first_or_404()
    
    # Determine current day
    current_day = topic.current_day + 1
    
    # Get today's content
    daily_content = DailyContent.query.filter_by(
        topic_id=topic.id, 
        day_number=current_day
    ).first()
    
    if not daily_content:
        flash('No content available for today. Please check back later.')
        return redirect(url_for('dashboard'))
    
    # Parse and structure content for pagination
    try:
        notes = json.loads(daily_content.notes)
        flashcards = json.loads(daily_content.flashcards)
        today_quiz = json.loads(daily_content.today_quiz)
        overall_quiz = json.loads(daily_content.overall_quiz)
    except json.JSONDecodeError:
        flash('Error loading content. Please try again.')
        return redirect(url_for('dashboard'))
    
    # Create paginated structure
    pages = []
    
    # Add intro page
    pages.append({
        'type': 'intro',
        'title': f'Day {current_day}',
        'content': f'Welcome to Day {current_day} of {topic.title}!'
    })
    
    # Add notes pages (each note gets its own page + explanation pages)
    if 'notes' in notes:
        for i, note in enumerate(notes['notes']):
            # Note title page
            pages.append({
                'type': 'note_title',
                'title': note['title'],
                'content': note['title'],
                'note_index': i
            })
            
            # Note content page
            pages.append({
                'type': 'note_content',
                'title': note['title'],
                'content': note['content'],
                'note_index': i
            })
    
    # Add flashcard intro
    pages.append({
        'type': 'section_intro',
        'title': 'Practice Time!',
        'content': 'Let\'s reinforce what you\'ve learned with some flashcards.'
    })
    
    # Add flashcards (each flashcard gets its own page)
    if 'flashcards' in flashcards:
        for i, flashcard in enumerate(flashcards['flashcards']):
            pages.append({
                'type': 'flashcard',
                'front': flashcard['front'],
                'back': flashcard['back'],
                'note_reference': flashcard.get('note_reference', 'General'),
                'flashcard_index': i
            })
    
    # Add quiz intro
    pages.append({
        'type': 'section_intro',
        'title': 'Quiz Time!',
        'content': 'Now let\'s test your understanding with today\'s quiz.'
    })
    
    # Add today's quiz questions (each question gets its own page)
    if 'quiz' in today_quiz:
        for i, question in enumerate(today_quiz['quiz']):
            pages.append({
                'type': 'quiz_question',
                'quiz_type': 'today',
                'question': question,
                'question_index': i
            })
    
    # Add overall quiz intro
    pages.append({
        'type': 'section_intro',
        'title': 'Comprehensive Review',
        'content': 'Finally, let\'s review everything you\'ve learned so far.'
    })
    
    # Add overall quiz questions
    if 'quiz' in overall_quiz:
        for i, question in enumerate(overall_quiz['quiz']):
            pages.append({
                'type': 'quiz_question',
                'quiz_type': 'overall',
                'question': question,
                'question_index': i
            })
    
    # Add completion page
    pages.append({
        'type': 'completion',
        'title': 'Great Job!',
        'content': f'You\'ve completed Day {current_day}!'
    })
    
    return render_template('learn_paginated.html', 
                         topic=topic, 
                         day=current_day,
                         pages=pages,
                         total_pages=len(pages))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0') 
