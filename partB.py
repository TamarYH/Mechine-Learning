import numpy as np
import pandas as pd
import sns
from matplotlib import pyplot as plt
from nltk import SnowballStemmer
import re
from scipy.stats import randint
from datetime import datetime
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_selection import SelectKBest, chi2, RFE
from sklearn.model_selection import train_test_split, KFold, GridSearchCV, RandomizedSearchCV
from sklearn.metrics import roc_auc_score, accuracy_score, confusion_matrix
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier, export_graphviz, plot_tree
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler, MinMaxScaler

df = pd.read_pickle(r"C:\Users\tamar\Downloads\XY_train.pkl")
print(df)


# 1.Preprocessing ----------------------------------------------------------------------------------------------->
def preprocess_data(df):
    # Drop rows with missing values exceeding a threshold
    df = df.dropna(thresh=df.shape[1] - 2)

    # Fill missing values for 'email' with 'unknown'
    df['email'] = df['email'].fillna('unknown')

    # Fill missing values for 'embedded_content' and 'platform' based on their probability distributions
    embedded_content_prob = df['embedded_content'].value_counts(normalize=True)
    platform_prob = df['platform'].value_counts(normalize=True)

    def impute_missing_values(row, prob_dist):
        if pd.isnull(row):
            return np.random.choice(prob_dist.index, p=prob_dist.values)
        else:
            return row

    df['embedded_content'] = df['embedded_content'].apply(lambda x: impute_missing_values(x, embedded_content_prob))
    df['platform'] = df['platform'].apply(lambda x: impute_missing_values(x, platform_prob))

    # Fill missing values for 'email_verified' and 'blue_tick'
    df['email_verified'].fillna(df['blue_tick'], inplace=True)
    df['blue_tick'].fillna(df['email_verified'], inplace=True)

    # Fill missing values for 'gender' randomly
    df['gender'].replace('None', np.nan, inplace=True)
    gender_counts = df['gender'].value_counts()
    df['gender'].fillna(pd.Series(np.random.choice(gender_counts.index, size=len(df.index),
                                                   p=(gender_counts / gender_counts.sum()))), inplace=True)

    # Delete the remaining rows with missing values
    df = df.dropna()

    # Convert 'message_date' to categorical and create 'message_time_category' column
    df['message_date'] = pd.to_datetime(df['message_date'])
    df['hour'] = df['message_date'].dt.hour
    morning_interval = range(6, 12)  # 6:00 AM to 11:59 AM
    noon_interval = range(12, 18)  # 12:00 PM to 5:59 PM
    evening_interval = range(18, 24)  # 6:00 PM to 11:59 PM

    def categorize_hour(hour):
        if hour in morning_interval:
            return 'Morning'
        elif hour in noon_interval:
            return 'Noon'
        elif hour in evening_interval:
            return 'Evening'
        else:
            return 'Night'

    df['message_time_category'] = df['hour'].apply(categorize_hour)
    df.drop(columns=['hour'], inplace=True)

    # handle changes for ngram
    # Make a copy of the DataFrame to avoid modifying the original
    df_processed = df.copy()

    # Lowercase
    df_processed['clean_text'] = df_processed['text'].str.lower()

    # Stemming
    stemmer = SnowballStemmer('english')
    df_processed['clean_text'] = df_processed['clean_text'].apply(
        lambda x: ' '.join(stemmer.stem(word) for word in x.split()))

    # Remove punctuation
    df_processed['clean_text'] = df_processed['clean_text'].apply(lambda x: re.sub(r'[^\w\s]', ' ', x))

    # Remove numbers
    df_processed['clean_text'] = df_processed['clean_text'].apply(
        lambda x: ' '.join(word for word in x.split() if not word.isdigit()))

    # Remove words with 1 letter
    df_processed['clean_text'] = df_processed['clean_text'].apply(lambda x: re.sub(r'\b\w{1}\b', '', x))

    # Remove top 0.05% of most common or not common words
    h_pct = 0.05
    l_pct = 0.05

    # Remove the top $h_pct of the most frequent words
    high_freq = pd.Series(' '.join(df_processed['clean_text']).split()).value_counts()[
                :int(pd.Series(' '.join(df_processed['clean_text']).split()).count() * h_pct / 100)]
    df_processed['clean_text'] = df_processed['clean_text'].apply(
        lambda x: ' '.join(word for word in x.split() if word not in high_freq))

    # Remove the top $l_pct of the least frequent words
    low_freq = pd.Series(' '.join(df_processed['clean_text']).split()).value_counts()[
               :-int(pd.Series(' '.join(df_processed['clean_text']).split()).count() * l_pct / 100):-1]
    df_processed['clean_text'] = df_processed['clean_text'].apply(
        lambda x: ' '.join(word for word in x.split() if word not in low_freq))

    # Remove double spaces
    df_processed['clean_text'] = df_processed['clean_text'].apply(lambda x: re.sub(r'\s+', ' ', x))

    return df_processed


# 2.Feature Extraction ---------------------------------------------------------------------------------------------->

def extract_features(df):
    # 1. Create a new column based on the length of messages
    df['message_length'] = df['text'].apply(lambda x: len(x))

    # 2. Create the number of messages sent by the user
    df['num_messages_sent'] = df['previous_messages_dates'].apply(len)

    # 3. Create the number of followers and following
    df['follower_count'] = df['date_of_new_follower'].apply(lambda x: len(x))
    df['following_count'] = df['date_of_new_follow'].apply(lambda x: len(x))

    # 4. Append new columns created to the df
    new_columns_df = df[['follower_count', 'following_count']]

    # 5. N-GRAM
    X = df['clean_text']
    ngram_vectorizer = CountVectorizer(ngram_range=(1, 2), max_features=100)
    X_ngrams = ngram_vectorizer.fit_transform(X).toarray()
    df_output = pd.DataFrame(data=X_ngrams, columns=ngram_vectorizer.get_feature_names_out())

    # 6. Extract email domain endings
    def extract_email_domain_ending(email):
        if pd.isnull(email):
            return 'Missing'
        match = re.search(r'\.(\w+)$', email)
        if match:
            return match.group(1)
        else:
            return 'Unknown'

    df['email_domain_ending'] = df['email'].apply(extract_email_domain_ending)
    email_domain_ending_counts = df['email_domain_ending'].value_counts()

    # 7. Create seniority in years
    df['account_creation_date'] = pd.to_datetime(df['account_creation_date'])
    current_date = datetime.now()
    df['seniority'] = (current_date - df['account_creation_date']).dt.days / 365.25

    # 8. Create the average time difference between messages
    def calculate_average_time_difference(message_dates_array):
        message_dates = [datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S') for date_str in message_dates_array]
        time_diffs = []
        for i in range(1, len(message_dates)):
            time_diff = (message_dates[i] - message_dates[i - 1]).total_seconds()
            if time_diff < 0:
                time_diff = (message_dates[i - 1] - message_dates[i]).total_seconds()
            time_diffs.append(time_diff)
        if len(time_diffs) > 0:
            average_time_difference = sum(time_diffs) / len(time_diffs)
            average_time_difference = int(average_time_difference)
            return average_time_difference
        else:
            return None

    df['average_time_difference'] = df['previous_messages_dates'].apply(calculate_average_time_difference)

    return df, new_columns_df, df_output


# 3.Feature Representation -------------------------------------------------------------------------------------------->

def feature_representation(df, new_columns_df, df_output):
    max_value_list = list()
    # Normalize the values by dividing each column by its maximum value
    columns_to_normalize = ['message_length', 'num_messages_sent', 'follower_count', 'following_count', 'seniority']
    for column in columns_to_normalize:
        max_value = df[column].max()
        df[f'normalized_{column}'] = df[column] / max_value
        max_value_list.append(max_value)

    # One-hot coding
    email_domain_ending_onehot = pd.get_dummies(df['email_domain_ending'], prefix='email_ending')
    embedded_content_onehot = pd.get_dummies(df['embedded_content'], prefix='embedded_content')
    platform_onehot = pd.get_dummies(df['platform'], prefix='platform')
    message_time_category_onehot = pd.get_dummies(df['message_time_category'], prefix='message_time')
    df = pd.concat(
        [df, email_domain_ending_onehot, embedded_content_onehot, platform_onehot, message_time_category_onehot],
        axis=1)
    df.drop(['email_domain_ending', 'embedded_content', 'platform', 'message_time_category'], axis=1, inplace=True)

    # Convert one-hot coding to binary values
    bool_to_binary = {True: 1, False: 0}
    df['email_verified'] = df['email_verified'].map(bool_to_binary)
    df['blue_tick'] = df['blue_tick'].map(bool_to_binary)
    for column in df.columns:
        if df[column].dtype == bool:
            df[column] = df[column].astype(int)

    gender_to_binary = {'F': 1, 'M': 0}
    df['gender'] = df['gender'].map(gender_to_binary)

    # Normalization for the n-gram features
    scaler = MinMaxScaler()
    X_ngrams_normalized = scaler.fit_transform(df_output)
    df_normalized = pd.DataFrame(X_ngrams_normalized, columns=df_output.columns)
    df = pd.concat([df.reset_index(drop=True), df_normalized], axis=1)

    # Arrange data again
    df = df.drop(columns=['text', 'previous_messages_dates', 'message_date',
                          'email', 'date_of_new_follower', 'date_of_new_follow',
                          'account_creation_date', 'message_length', 'num_messages_sent',
                          'follower_count', 'following_count',
                          'seniority', 'clean_text', 'average_time_difference'])

    return df, max_value_list


# 4.Feature Selection -------------------------------------------------------------------------------------------->

def feature_selection(df, k):
    # Extract the target variable 'sentiment'
    Y = df['sentiment']

    # Drop the 'sentiment' column
    X_cat = df.drop(columns=['sentiment'])

    # Save 'textID' column for later
    textID_column = X_cat['textID']

    # Drop 'textID' and 'sentiment' columns from the feature DataFrame
    X_cat = X_cat.drop(columns=['textID'])

    # Initialize SelectKBest with chi-squared as the scoring function and k=k
    chi2_features = SelectKBest(chi2, k=k)

    # Fit SelectKBest to the data and transform it
    X_cat_kbest = chi2_features.fit_transform(X_cat, Y)

    # Get the selected features
    selected_features = X_cat.columns[chi2_features.get_support()]

    # Create a DataFrame with selected features
    selected_features_df = pd.DataFrame(X_cat_kbest, columns=selected_features)

    # Add 'textID' column back to the selected features DataFrame
    selected_features_df['sentiment'] = df['sentiment']

    return selected_features_df


# ----------------------------------TEST DATA------------------------------------------------------------------------>

def test_preprocess_data(df, x, y, z):
    test_df = list()
    # Gender: if it is a man it will be 0, if it is a woman it will be 1
    df['gender'] = df['gender'].map({'M': 0, 'F': 1})
    test_df.append(df['gender'])

    # Email verified: if it is written true it will be 1, if it is written false it will be 0
    df['email_verified'] = df['email_verified'].astype(int)
    test_df.append(df['email_verified'])

    # Blue tick: if it is written true it will be 1, if it is written false it will be 0
    df['blue_tick'] = df['blue_tick'].astype(int)
    test_df.append(df['blue_tick'])

    # Normalized number of messages sent
    df['normalized_num_messages_sent'] = df['previous_messages_dates'].apply(lambda x: len(x))/x
    test_df.append(df['normalized_num_messages_sent'])

    # Normalized follower count
    df['normalized_follower_count'] = df['date_of_new_follower'].apply(lambda x: len(x))/y
    test_df.append(df['normalized_follower_count'])

    # Normalized following count
    df['normalized_following_count'] = df['date_of_new_follow'].apply(lambda x: len(x))/z
    test_df.append(df['normalized_following_count'])

    # Embedded content mp4: if embedded_content = mp4 it will be 1, otherwise it will be 0
    df['embedded_content_mp4'] = df['embedded_content'].apply(lambda x: 1 if x == 'mp4' else 0)
    test_df.append(df['embedded_content_mp4'])

    # Platform Instagram: if platform = instagram it will be 1, otherwise 0
    df['platform_instagram'] = df['platform'].apply(lambda x: 1 if x == 'instagram' else 0)
    test_df.append(df['platform_instagram'])

    # Platform Telegram: if platform = telegram it will be 1, otherwise 0
    df['platform_telegram'] = df['platform'].apply(lambda x: 1 if x == 'telegram' else 0)
    test_df.append(df['platform_telegram'])

    # Message time Evening: if in the message_date column between 6:00 PM and 11:59 PM, it will be 1, otherwise 0
    df['message_date'] = pd.to_datetime(df['message_date'])
    df['message_time_Evening'] = df['message_date'].apply(lambda x: 1 if 18 <= x.hour < 24 else 0)
    test_df.append(df['message_time_Evening'])

    return pd.concat(test_df, axis=1)

# ----------------------------------PART B------------------------------------------------------------------------>

#SPLITTING THE DATA INTO TRAINING AND TESTING
# Step 1: Preprocess the data
df = preprocess_data(df)
# Define dataset (X, y)
X = df.drop(columns=['sentiment'])
Y = df['sentiment']
# Split the dataset into training and test sets
X_train, X_test, Y_train, Y_test = train_test_split(X, Y, test_size=0.2, random_state=42)
# Merge X_train and y_train into one DataFrame for feature extraction
train_df = pd.concat([X_train, Y_train], axis=1)
# Step 1: Extract features from the training data
train_features, new_columns_train, df_output_train = extract_features(train_df)
ngram_vectorizer = CountVectorizer(ngram_range=(1, 2), max_features=100)
X_train_ngrams = ngram_vectorizer.fit_transform(train_features['clean_text']).toarray()
# Step 2: Perform feature representation on training data
train_represented, max_value_list = feature_representation(train_features, new_columns_train, df_output_train)
# Step 3: Perform feature selection on training data
train_selected = feature_selection(train_represented, 10)
# step 4: slippling the data into X and y
X_train = train_selected.drop(columns=['sentiment'])
Y_train = train_selected['sentiment']
# Step 5: Preprocess the test data
x = max_value_list[1]
y = max_value_list[2]
z = max_value_list[3]
X_test = test_preprocess_data(X_test, x, y, z)

#------------------------------------------Decision Trees---------------------------------------------->

# CHOOSING THE BEST PARAMETERS FOR THE MODEL
param_dist = {
    'criterion': ['gini', 'entropy'],
    'max_depth': [None, 5, 10, 15],
    'min_samples_split': randint(2, 20),
    'min_samples_leaf': randint(1, 10),
    'min_impurity_decrease': [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
}
# Set random_state parameter for reproducibility
dt_classifier = DecisionTreeClassifier(random_state=42)
random_search = RandomizedSearchCV(estimator=dt_classifier, param_distributions=param_dist,
                                   n_iter=100, scoring='roc_auc', random_state=42)
random_search.fit(X_train, Y_train)
best_params = random_search.best_params_
print("Best Parameters:")
for param_name, param_value in best_params.items():
    print(f"{param_name}: {param_value}")
best_classifier = random_search.best_estimator_
y_pred_proba = best_classifier.predict_proba(X_test)
auc_roc = roc_auc_score(Y_test, y_pred_proba[:, 1]) # Use the probabilities of the positive class
print("AUC-ROC Score:", auc_roc)
plt.figure(figsize=(20,10))
plot_tree(best_classifier, feature_names=X_train.columns, class_names=best_classifier.classes_,
          filled=True, rounded=True)
plt.show()


#FEATURE IMPORTANCE
# Get feature importances
feature_importances = best_classifier.feature_importances_
# Get feature names
feature_names = X_train.columns
# Create a DataFrame with feature importances
feature_importances_df = pd.DataFrame({'feature': feature_names, 'importance': feature_importances})
# Sort the DataFrame by importance
feature_importances_df = feature_importances_df.sort_values('importance', ascending=False)
# Plot the feature importances
plt.figure(figsize=(20,10))
plt.barh(feature_importances_df['feature'], feature_importances_df['importance'])
plt.xlabel('Importance')
plt.ylabel('Feature')
plt.title('Feature Importances')
plt.show()


#------------------------------------------Artificial Neural Networks---------------------------------------------->

# Initialize the MLPClassifier with default values or specify parameters
model = MLPClassifier(random_state=42)
# Train the classifier
model.fit(X_train, Y_train)
# Step 4: Model Evaluation
# Predict probabilities on training set
y_train_pred_proba = model.predict_proba(X_train)[:, 1]
# Predict probabilities on validation set
y_val_pred_proba = model.predict_proba(X_test)[:, 1]
# Calculate AUC-ROC
train_auc = roc_auc_score(Y_train, y_train_pred_proba)
val_auc = roc_auc_score(Y_test, y_val_pred_proba)
print("Training AUC-ROC:", train_auc)
print("Validation AUC-ROC:", val_auc)

#HYPERPARAMETER TUNING
#1111111111111111111111111111111111
# Define the hyperparameter grid
param_grid = {
    'max_iter': [100, 200, 300, 400, 500, 600, 700]
}
# Initialize lists to store accuracy values
train_auc_list = []
val_auc_list = []
max_iter_list = []
# Initialize the classifier
model = MLPClassifier(random_state=42)
# Initialize GridSearchCV
grid_search = GridSearchCV(model, param_grid, cv=5, scoring='roc_auc', n_jobs=-1)
# Fit GridSearchCV
grid_search.fit(X_train, Y_train)
# Get the results for each iteration
for i, params in enumerate(grid_search.cv_results_['params']):
    max_iter = params['max_iter']
    mean_fit_time = grid_search.cv_results_['mean_fit_time'][i]
    mean_test_score = grid_search.cv_results_['mean_test_score'][i]

    max_iter_list.append(max_iter)
    train_auc_list.append(mean_fit_time)
    val_auc_list.append(mean_test_score)

    print(f"Max Iterations: {max_iter}, Training Time: {mean_fit_time}, Validation AUC-ROC: {mean_test_score}")
# Plot the results
plt.figure(figsize=(10, 6))
plt.plot(max_iter_list, train_auc_list, label='Training Time')
plt.plot(max_iter_list, val_auc_list, label='Validation AUC-ROC')
plt.xlabel('Max Iterations')
plt.ylabel('Score')
plt.title('Model Performance vs Max Iterations')
plt.legend()
plt.grid(True)
plt.show()

#222222222222222222222222222222222222222
# Define the hyperparameter grid
param_grid = {
    'hidden_layer_sizes': [(10,), (50,), (100,), (10, 10), (50, 50), (100, 100)]
}
# Initialize lists to store accuracy values
train_auc_list = []
val_auc_list = []
hidden_layer_sizes_list = []
# Initialize the classifier
model = MLPClassifier(random_state=42)
# Initialize GridSearchCV
grid_search = GridSearchCV(model, param_grid, cv=5, scoring='roc_auc',return_train_score=True, n_jobs=-1)
# Fit GridSearchCV
grid_search.fit(X_train, Y_train)
# Get the results for each iteration
for i, params in enumerate(grid_search.cv_results_['params']):
    hidden_layer_sizes = params['hidden_layer_sizes']
    hidden_layer_sizes_str = str(hidden_layer_sizes)  # Convert to string representation
    mean_train_score = grid_search.cv_results_['mean_train_score'][i]
    mean_test_score = grid_search.cv_results_['mean_test_score'][i]

    hidden_layer_sizes_list.append(hidden_layer_sizes)
    train_auc_list.append(mean_train_score)
    val_auc_list.append(mean_test_score)

    print(f"Hidden Layer Sizes: {hidden_layer_sizes}, Training AUC-ROC: {mean_train_score}, Validation AUC-ROC: {mean_test_score}")
#print the best hyperparameters
print("Best hyperparameters:", grid_search.best_params_)

#33333333333333333333333333333333333333333
# Define the activation functions
activation_functions = ['identity', 'logistic', 'tanh', 'relu']
# Initialize an empty list to store the ROC AUC scores
roc_auc_scores = []

# Loop through each activation function
for activation in activation_functions:
    # Initialize the MLPClassifier with the specified activation function
    model = MLPClassifier(activation=activation, random_state=42)
    # Train the classifier
    model.fit(X_train, Y_train)
    # Predict probabilities on validation set
    y_val_pred_proba = model.predict_proba(X_test)[:, 1]
    # Calculate ROC AUC score
    roc_auc = roc_auc_score(Y_test, y_val_pred_proba)
    # Append the ROC AUC score to the list
    roc_auc_scores.append(roc_auc)

# Create a DataFrame with the activation functions and their ROC AUC scores
activation_results = pd.DataFrame({'activation': activation_functions, 'ROC AUC': roc_auc_scores})
print(activation_results)

# 44444444444444444444444444444444
solver_activation_map = {
    'lbfgs': 'tanh',
    'sgd': 'tanh',
    'adam': 'tanh'
}
solver_roc_auc_scores = []

# Loop through each solver
for solver, activation in solver_activation_map.items():
    # Initialize the MLPClassifier with the specified solver and activation function
    model = MLPClassifier(solver=solver, activation=activation, random_state=42)
    # Train the classifier
    model.fit(X_train, Y_train)
    # Predict probabilities on validation set
    y_val_pred_proba = model.predict_proba(X_test)[:, 1]
    # Calculate ROC AUC score
    roc_auc = roc_auc_score(Y_test, y_val_pred_proba)
    # Append the ROC AUC score to the list
    solver_roc_auc_scores.append(roc_auc)

# Create a DataFrame with the solvers and their corresponding ROC AUC scores
roc_auc_df = pd.DataFrame({'solver': list(solver_activation_map.keys()), 'roc_auc': solver_roc_auc_scores})
print(roc_auc_df)

#CONFUSION MATRIX FOR THE BEST MODEL
# Initialize the MLPClassifier with specified parameters
model = MLPClassifier(random_state=42,
                      max_iter=400,
                      hidden_layer_sizes=(50, 50),
                      activation='tanh',
                      solver='adam')

# Train the classifier
model.fit(X_train, Y_train)

# Predict probabilities on training set
y_train_pred_proba = model.predict_proba(X_train)[:, 1]

# Predict probabilities on validation set
y_val_pred_proba = model.predict_proba(X_test)[:, 1]

# Calculate AUC-ROC
train_auc = roc_auc_score(Y_train, y_train_pred_proba)
val_auc = roc_auc_score(Y_test, y_val_pred_proba)
print("Training AUC-ROC:", train_auc)
print("Validation AUC-ROC:", val_auc)

# Create a confusion matrix
y_pred = model.predict(X_test)
conf_matrix = confusion_matrix(Y_test, y_pred)
print("Confusion Matrix:\n", conf_matrix)

# Plot the confusion matrix
sns.heatmap(conf_matrix, annot=True, fmt='d', cmap='Blues')
plt.xlabel('Predicted')
plt.ylabel('Actual')
plt.title('Confusion Matrix')
plt.show()

# ------------------------------------------SVN---------------------------------------------->
C_values = np.arange(1, 2.1, 0.1)
auc_scores = []
for C in C_values:
    model = LinearSVC(C=C, random_state=42)
    model.fit(X_train, Y_train)
    Y_test_pred = model.decision_function(X_test)
    auc_score = roc_auc_score(Y_test, Y_test_pred)
    auc_scores.append(auc_score)

print(pd.DataFrame({'C': C_values, 'AUC-ROC': auc_scores}))
auc_scores_df = pd.DataFrame({'C': C_values, 'AUC-ROC': auc_scores})
optimal_C = auc_scores_df.loc[auc_scores_df['AUC-ROC'].idxmax(), 'C']
optimal_model = LinearSVC(C=optimal_C, random_state=42)
optimal_model.fit(X_train, Y_train)
coefficients = optimal_model.coef_
print("Coefficients:", coefficients)





