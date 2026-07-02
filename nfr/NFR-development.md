# Non-Functional Requirements

These non-functional requirements (NFRs) apply the project in this workspace and each submodules. They are mandatory and independent of the functional requirements of any individual task. Even when a task description says nothing about architecture, logging, testing, or documentation, you MUST satisfy everything below. When a functional requirement conflicts with an NFR, stop and raise the conflict rather than silently dropping an NFR.

---

## 1. Architecture

- You MUST design the whole system in a modular way.
- Even if you receive the requirements for a single product, you MUST NOT architect that product as one monolithic entity. You MUST break it down into modules.
- Each module MUST be designed as a self-contained application. Each module MUST clearly define its inputs and outputs.
- Each module MUST have its own test suite (see *Testing*).
- Each module MUST have its own documentation (see *Documentation*).

---

## 2. Logging

- Every functionality MUST have logging.
- Every error encountered in the execution of code or agents MUST be logged.
- Every piece of information useful for determining the correct inner working of agents or code MUST be logged.
- Logs MUST be written in **JSONL** format (one JSON object per line).
- All modules must log in a centralized logs/ folder in the main project folder
- Each JSONL record must indicate the **Environment** (test/prod) producing the log

---

## 3. Testing

- You MUST always create an executable test suite as you implement a new feature. The test suite MUST be executable as a shell or Python script **outside of Claude** — i.e. a developer can run it directly from a terminal with no agent involvement.
- All test scripts for all functionalities MUST be invocable from a **single project-wide test script**.
- You MUST maintain a clean separation between test data and production data. Whether the project uses a database or other storage, the separation MUST be clean and explicit.
  - All test scripts MUST run against the **test** storage or database.
  - You MUST NOT run tests against production storage or production databases.

---

## 4. Documentation

- The whole project, as well as every single module, MUST be thoroughly documented.
- The architecture of the project, as well as the architecture of every single module, MUST be documented in a dedicated architecture file.
- The project MUST have a **user guide**. The user guide explains how to use the project from the **user's** point of view, not from the developer's point of view.

---

## 5. Configuration

- You MUST NOT hardcode in the code constants that can be changed to modify the behavior or the environment of the running software. All such values belong to a single configuration file per project, docated in the main project folder
- Example of values that must not be hard-coded in the code, but must be defined in the configuration file
    - username, passwords, API keys
    - name of AI models used in agents
    - numeric constants
- Prompts MUST be isolated in separate files, one prompt in one file, unless the prompt is very simple (one line). 
- These prompt files MUST be contained in the module that makes use of the prompt. In case multiple modules make use of the same prompt, then you will place such prompt into a prompts/ folder under the main project folder. The goal of having prompts in dedicated files is to allow the human developer to change prompts independently.

---

## 6. Implementation

- The architecture of the system is modular. Every module is self-contained in one folder.
- You MUST NOT declare any implementation task complete UNTIL you have created automated tests to verify it, and run successfully automated tests
- As a consequence, you must build a test harness for each task implemented, including UI and Web
- When you implement, you iterate until the test harness works successfully. You must not ask the user to test. You must not declare a task working until you proved through your test harness to be working
- You MUST provide a shell script to run each module
- You MUST provide a shell script to allow the initial setup of the whole project. Such shell script MUST be usable to update the setup of the project as well
- Follow Strictly YAGNI principles. Do not write code that is not needed. Do not be verbose in writing codes or in giving explanation to the user.

---

## 7. Safety

- You MUST NOT push to Github unless instructed
