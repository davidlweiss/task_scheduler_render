from flask import Flask, request, jsonify, send_from_directory
import pandas as pd
import os
import json
from datetime import datetime

app = Flask(__name__, static_folder='static')

# File paths
TASKS_FILE = 'data/tasks.csv'
FREE_TIME_FILE = 'data/free_time.csv'

# Helper functions to load/save data
def load_data(file_path, default_columns):
    if os.path.exists(file_path):
        return pd.read_csv(file_path)
    else:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        df = pd.DataFrame(columns=default_columns)
        df.to_csv(file_path, index=False)
        return df

def save_data(df, file_path):
    # Ensure directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    df.to_csv(file_path, index=False)

# API Routes
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

# Get all tasks
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    tasks_df = load_data(TASKS_FILE, ['Project', 'Task', 'Estimated Time', 'Due Date', 'Importance', 'Complexity'])
    return jsonify(tasks_df.to_dict(orient='records'))

# Save tasks
@app.route('/api/tasks', methods=['POST'])
def save_tasks():
    tasks = request.json
    tasks_df = pd.DataFrame(tasks)
    save_data(tasks_df, TASKS_FILE)
    return jsonify({"status": "success"})

# Get all free time slots
@app.route('/api/free-time', methods=['GET'])
def get_free_time():
    free_time_df = load_data(FREE_TIME_FILE, ['Date', 'Available Hours'])
    return jsonify(free_time_df.to_dict(orient='records'))

# Save free time slots
@app.route('/api/free-time', methods=['POST'])
def save_free_time():
    free_time = request.json
    free_time_df = pd.DataFrame(free_time)
    save_data(free_time_df, FREE_TIME_FILE)
    return jsonify({"status": "success"})

# Run scheduler
@app.route('/api/run-scheduler', methods=['POST'])
def run_scheduler():
    data = request.json
    tasks_df = pd.DataFrame(data.get('tasks', []))
    free_time_df = pd.DataFrame(data.get('freeTime', []))
    
    # Convert date strings to datetime objects
    if not tasks_df.empty and 'Due Date' in tasks_df.columns:
        tasks_df['Due Date'] = pd.to_datetime(tasks_df['Due Date'], errors='coerce')
    
    if not free_time_df.empty and 'Date' in free_time_df.columns:
        free_time_df['Date'] = pd.to_datetime(free_time_df['Date'])
    
    # SCHEDULING LOGIC
    scheduled_tasks = []
    warnings = []
    large_tasks = []
    
    # Exit early if no tasks or free time
    if tasks_df.empty or free_time_df.empty:
        return jsonify({
            'scheduledTasks': [],
            'warnings': ["No tasks or free time available for scheduling."],
            'largeTasks': []
        })
    
    # Basic scheduling logic
    working_free_time_df = free_time_df.copy()
    working_free_time_df = working_free_time_df.sort_values(by='Date')
    
    total_free_time = working_free_time_df['Available Hours'].sum()
    total_task_time = tasks_df['Estimated Time'].sum() if 'Estimated Time' in tasks_df.columns else 0
    
    # Check for large tasks
    for idx, task in tasks_df.iterrows():
        # Skip if necessary columns don't exist
        if 'Estimated Time' not in task or 'Task' not in task:
            continue
            
        task_time = task['Estimated Time']
        task_name = task['Task']
        
        # Check if it's a large task
        if task_time > 6 and not any(tag in str(task_name) for tag in ['[MULTI-SESSION]', '[FIXED EVENT]', '[PENDING PLANNING]']):
            large_tasks.append({
                'id': idx,
                'Task': task_name,
                'Estimated Time': task_time,
                'Due Date': task['Due Date'] if 'Due Date' in task and pd.notnull(task['Due Date']) else None
            })
            
            warnings.append(f"Task '{task_name}' exceeds 6 hours and should probably be split unless it's a Work Block.")
    
    # Prioritize tasks
    if not tasks_df.empty and 'Due Date' in tasks_df.columns and 'Importance' in tasks_df.columns:
        today = pd.to_datetime(datetime.today().date())
        
        def calc_priority(row):
            days_until_due = (row['Due Date'] - today).days if pd.notnull(row['Due Date']) else 9999
            return days_until_due * 1 - row['Importance'] * 5
        
        tasks_df['Priority Score'] = tasks_df.apply(calc_priority, axis=1)
        tasks_df = tasks_df.sort_values(by=['Priority Score', 'Complexity'])
    
    # Allocate tasks to free time windows
    for _, task in tasks_df.iterrows():
        # Skip if necessary columns don't exist
        if 'Estimated Time' not in task or 'Task' not in task:
            continue
            
        task_time_remaining = task['Estimated Time']
        task_name = task['Task']
        due_date = task['Due Date'] if 'Due Date' in task else None
        
        for f_idx, window in working_free_time_df.iterrows():
            if task_time_remaining <= 0:
                break
            
            if pd.notnull(due_date) and window['Date'] > due_date:
                break
            
            available_hours = window['Available Hours']
            if available_hours > 0:
                allocated_time = min(task_time_remaining, available_hours)
                scheduled_tasks.append({
                    'Task': task_name,
                    'Date': window['Date'].strftime('%Y-%m-%d'),
                    'Allocated Hours': allocated_time
                })
                working_free_time_df.at[f_idx, 'Available Hours'] -= allocated_time
                task_time_remaining -= allocated_time
        
        # Check if we couldn't schedule everything before due date
        if pd.notnull(due_date) and task_time_remaining > 0:
            warnings.append(
                f"HANDLE: {task_name} (Due: {due_date.strftime('%Y-%m-%d')}) "
                f"needs {task['Estimated Time']}h, but only {task['Estimated Time'] - task_time_remaining}h scheduled before due date."
            )
    
    # Calculate daily summary for the response
    daily_summary = []
    if not working_free_time_df.empty:
        for date, group in working_free_time_df.groupby('Date'):
            daily_summary.append({
                'Date': date.strftime('%Y-%m-%d'),
                'Total Available': group['Available Hours'].sum(),
                'Total Scheduled': 0  # Will be updated below
            })
    
    # Update scheduled hours in daily summary
    for task in scheduled_tasks:
        date = task['Date']
        hours = task['Allocated Hours']
        
        for summary in daily_summary:
            if summary['Date'] == date:
                summary['Total Scheduled'] += hours
                break
    
    # Return the results
    return jsonify({
        'totalFreeTime': total_free_time,
        'totalTaskTime': total_task_time,
        'scheduledTasks': scheduled_tasks,
        'dailySummary': daily_summary,
        'warnings': warnings,
        'largeTasks': large_tasks
    })

# Task breakdown endpoint
@app.route('/api/breakdown-task', methods=['POST'])
def breakdown_task():
    data = request.json
    task_id = int(data.get('taskId'))  # Convert to int as it comes as string from frontend
    approach = data.get('approach')
    params = data.get('params', {})
    
    # Load current tasks
    tasks_df = load_data(TASKS_FILE, ['Project', 'Task', 'Estimated Time', 'Due Date', 'Importance', 'Complexity'])
    
    # Check if task exists
    if task_id >= len(tasks_df):
        return jsonify({"status": "error", "message": "Task not found"}), 404
    
    # Get the task
    task = tasks_df.iloc[task_id]
    task_name = task['Task']
    hours = task['Estimated Time']
    
    # Handle different approaches
    if approach == "planning":
        # Create planning task and mark original as pending
        planning_task_name = params.get('taskName', f"Plan breakdown of: {task_name}")
        planning_date = params.get('date')
        planning_hours = params.get('hours', 1.0)
        
        # Create new planning task
        new_task = pd.DataFrame({
            'Project': [task['Project'] if 'Project' in task else "Planning"],
            'Task': [planning_task_name],
            'Estimated Time': [planning_hours],
            'Due Date': [planning_date],
            'Importance': [4],  # High importance
            'Complexity': [2]   # Moderate complexity
        })
        
        # Update original task
        tasks_df.at[task_id, 'Task'] = f"{task_name} [PENDING PLANNING]"
        
        # Add new task
        tasks_df = pd.concat([tasks_df, new_task], ignore_index=True)
        
    elif approach == "breakdown":
        # Break into subtasks
        subtasks = params.get('subtasks', [])
        
        if not subtasks:
            return jsonify({"status": "error", "message": "No subtasks provided"}), 400
        
        # Create new subtasks
        new_tasks = []
        for subtask in subtasks:
            new_task = dict(task)
            new_task['Task'] = subtask['name']
            new_task['Estimated Time'] = subtask['hours']
            new_tasks.append(new_task)
        
        # Remove original task
        tasks_df = tasks_df.drop(task_id)
        
        # Add new tasks
        new_tasks_df = pd.DataFrame(new_tasks)
        tasks_df = pd.concat([tasks_df, new_tasks_df], ignore_index=True)
        
    elif approach == "focus":
        # Split into focus sessions
        session_length = params.get('sessionLength', 2.0)
        num_sessions = params.get('numSessions', 0)
        update_name = params.get('updateName', True)
        new_name = params.get('newName', f"{task_name} [MULTI-SESSION]")
        
        # Update task
        if update_name:
            tasks_df.at[task_id, 'Task'] = new_name
        
        # Add focus session metadata
        if 'Focus Sessions' not in tasks_df.columns:
            tasks_df['Focus Sessions'] = None
        if 'Session Length' not in tasks_df.columns:
            tasks_df['Session Length'] = None
        
        tasks_df.at[task_id, 'Focus Sessions'] = num_sessions
        tasks_df.at[task_id, 'Session Length'] = session_length
        
    elif approach == "iterative":
        # Create iterative project structure
        exploration_hours = params.get('explorationHours', 2.0)
        
        # Create exploration task
        exploration_task = dict(task)
        exploration_task['Project'] = f"Iterative: {task_name}"
        exploration_task['Task'] = f"Initial exploration: {task_name}"
        exploration_task['Estimated Time'] = exploration_hours
        
        # Create remaining work task
        remaining_task = dict(task)
        remaining_task['Project'] = f"Iterative: {task_name}"
        remaining_task['Task'] = f"{task_name} [REMAINING WORK]"
        remaining_task['Estimated Time'] = hours - exploration_hours
        
        # Remove original task
        tasks_df = tasks_df.drop(task_id)
        
        # Add new tasks
        exploration_df = pd.DataFrame([exploration_task])
        remaining_df = pd.DataFrame([remaining_task])
        tasks_df = pd.concat([tasks_df, exploration_df, remaining_df], ignore_index=True)
        
    elif approach == "fixed":
        # Mark as fixed event
        update_name = params.get('updateName', True)
        new_name = params.get('newName', f"{task_name} [FIXED EVENT]")
        
        # Update task
        if update_name:
            tasks_df.at[task_id, 'Task'] = new_name
        
        # Add event type metadata
        if 'Event Type' not in tasks_df.columns:
            tasks_df['Event Type'] = None
        
        tasks_df.at[task_id, 'Event Type'] = "Fixed Duration"
    
    else:
        return jsonify({"status": "error", "message": "Invalid approach"}), 400
    
    # Save updated tasks
    save_data(tasks_df, TASKS_FILE)
    
    return jsonify({"status": "success"})

if __name__ == '__main__':
    # Use the PORT environment variable provided by Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)