### **The Loan Eligibility Engine (SDE Intern Backend)**

**Mission:** Your challenge is to design, build, and deploy an automated system that ingests user data, discovers personal loan products from public websites, matches users to eligible products, and notifies them. This project tests your ability to integrate cloud services with a powerful, self-hosted workflow automation engine. This assignment is objective-based; while we provide a framework, we highly value creative solutions and architectural decisions that improve performance, reduce cost, and demonstrate a deeper understanding of the problem space.

**Core Technologies:**

* **Backend Language:** Python or Go  
* **Cloud Platform:** AWS (Lambda, RDS for PostgreSQL, SES for email). API Gateway is optional. Local deployment is acceptable; public deployment on AWS is a plus, not a requirement. You are not expected to incur cloud costs.  
* **AI & Workflow:**  
  * A self-hosted **n8n** instance for workflow automation.  
  * Use an LLM API you have access to for specific AI tasks. Paid access is not required.  
* **Deployment:** Serverless Framework (for AWS resources) & Docker Compose (for n8n)

### **Functional Requirements**

#### **1\. Scalable User Data Ingestion**

* **Bulk CSV Upload:** Design a robust and scalable mechanism for uploading a CSV file containing user data (e.g., user\_id, email, monthly\_income, credit\_score, employment\_status, age). **Note:** A direct upload through a standard Lambda-backed API Gateway endpoint can hit request size and timeout limits. We are looking for a more professional, event-driven pattern.  
* **Data Persistence:** Once uploaded, the data must be parsed and stored in a dedicated table within an Amazon RDS for PostgreSQL database.

#### **2\. The n8n Automation Engine**

You must set up and configure a self-hosted n8n instance using Docker. All core business logic will be orchestrated through n8n workflows. You will need to design and build the following interconnected workflows:

* **Workflow A: Loan Product Discovery (The Web Crawler)**  
  * **Goal:** To autonomously find and store personal loan product information.  
  * **Trigger:** This workflow should run on a schedule (e.g., once daily).  
  * **Process:**  
    1. Crawl a list of public financial websites (you can choose 2-3 relevant sites).  
    2. Extract key details for personal loan products, such as product\_name, interest\_rate, and the **eligibility criteria** (e.g., minimum monthly income, required credit score range).  
    3. Store this structured data in a new loan\_products table in your PostgreSQL database. Handling changes in website layouts is optional.  
* **Workflow B: User-Loan Matching (The Eligibility Filter)**  
  * **Goal:** To match users from your database with the loan products they are likely eligible for.  
  * **Trigger:** This workflow should be triggered via a webhook after a new CSV of users has been successfully processed.  
  * **Process:**  
    1. Fetch the newly added user records from the PostgreSQL database.  
    2. Fetch the entire list of available loan products and their criteria from the database.  
    3. Implement the core matching logic within n8n.  
    4. For every successful match, store the result (e.g., linking user\_id to product\_id) in a matches table.  
  * **Creative Challenge: The Optimization Treasure Hunt**  
    1. **Problem:** Filtering thousands of users against dozens of loan products can be slow. Using a powerful LLM (like Gemini/GPT) to evaluate every single user-product pair would be extremely slow and expensive.  
    2. **Your Task:** Design and implement a multi-stage, optimized filtering pipeline. Show your creativity here. How can you reduce the number of user-product pairs before using an LLM for qualitative checks? For example, use a fast SQL-based pre-filter followed by more complex checks in n8n, or explain another approach.  
* **Workflow C: User Notification**  
  * **Goal:** To inform users about the loan products they have been matched with.  
  * **Trigger:** This workflow should run after the matching process (Workflow B) is complete.  
  * **Process:**  
    1. For each user who has new matches, construct a personalized email.  
    2. Using the AWS SES node in n8n, send the email to a test inbox you control, listing the loan products the matched user may qualify for. Do not email the sample CSV addresses. Use test-inbox aliases such as abcd+one@gmail.com and abcd+two@gmail.com (replace abcd with your own Gmail username) to prove the notification workflow works. Show one received email in the demonstration video.

#### **3\. User Interface**

* Provide a minimal UI that allows for the initial upload of the user data CSV file. This is the primary interaction point for starting the entire pipeline.

### **Deliverables**

1. **GitHub Repository:** A private repository containing all source code (Lambda functions, etc.) and configuration files, with GitHub users ujjwalkala-clickpe and HarshSrivastav-Dailype invited as collaborators.  
2. **Infrastructure & Automation Files:**  
   * A serverless.yml file to deploy all required AWS resources.  
   * A docker-compose.yml file for launching your self-hosted n8n instance.  
   * The JSON files for each of your n8n workflows.  
3. **Comprehensive Documentation (README.md):**  
   * **Architecture Diagram:** A clear diagram showing how AWS services and the n8n workflows all interact.  
   * **Setup & Deployment:** Step-by-step instructions to deploy the AWS stack and launch the n8n container. This must include how to configure all necessary credentials (AWS, database) within n8n.  
   * **Design Decisions:** A brief explanation of your workflow design, web crawling strategy, and especially your solution to the **Optimization Treasure Hunt**.  
4. **Demonstration Video (5-10 minutes):**  
   * A walkthrough of your n8n workflows, explaining the logic of each node.  
   * A live, end-to-end demonstration of the entire pipeline.  
   * Show an email received in a test inbox after the pipeline completes.

### **Submission**

Complete the assignment and submit your GitHub repository and demonstration video links through the submission form within 72 hours of receiving the assignment email.

Submission form: [https\://docs.google.com/forms/d/e/1FAIpQLSe1ET4U8rOcPM3VGJ8fT5HhigTO9fKuDlsB\_wHtB5acWys9XQ/viewform](https://docs.google.com/forms/d/e/1FAIpQLSe1ET4U8rOcPM3VGJ8fT5HhigTO9fKuDlsB_wHtB5acWys9XQ/viewform)

### **Evaluation Criteria**

Your submission will be evaluated based on the following criteria:

| Category (Max Points) | Description |
| :---- | :---- |
| **n8n Workflow Design & Automation (30)** | Sophistication, robustness, and efficiency of your n8n workflows. Creative use of n8n nodes for crawling, data manipulation, and logic. Your solution to the **Optimization Treasure Hunt** will be a key factor. |
| **Cloud Architecture & Integration (20)** | Quality of the AWS serverless design. Seamless and secure integration between AWS services and the self-hosted n8n instance. Scalability of the data ingestion method. |
| **Backend Functionality & Code Quality (30)** | Correct implementation of the data ingestion pipeline. Clean, modular, and well-commented code. |
| **Documentation & Presentation (20)** | Clarity of the architecture diagram and README.md. Quality and comprehensiveness of the video demonstration. |
| **Total (100)** |  |

Sample data (users.csv): Download the CSV below and use it to test your solution.

[https\://drive.google.com/file/d/1KBaZRkfr7T2kVBKSr\_2fr7v2tTe6f8qu/view?usp=sharing](https://drive.google.com/file/d/1KBaZRkfr7T2kVBKSr_2fr7v2tTe6f8qu/view?usp=sharing)