SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `password_resets`;
DROP TABLE IF EXISTS `notification_preferences`;
DROP TABLE IF EXISTS `notifications`;
DROP TABLE IF EXISTS `recurring_income`;
DROP TABLE IF EXISTS `recurring_expenses`;
DROP TABLE IF EXISTS `settlements`;
DROP TABLE IF EXISTS `goal_contributions`;
DROP TABLE IF EXISTS `savings_goals`;
DROP TABLE IF EXISTS `budgets`;
DROP TABLE IF EXISTS `income`;
DROP TABLE IF EXISTS `expenses`;
DROP TABLE IF EXISTS `categories`;
DROP TABLE IF EXISTS `accounts`;
DROP TABLE IF EXISTS `users`;

CREATE TABLE `users` (
  `user_id` INT AUTO_INCREMENT PRIMARY KEY,
  `username` VARCHAR(100) NOT NULL UNIQUE,
  `email` VARCHAR(255) NULL UNIQUE,
  `password` VARCHAR(255) NOT NULL,
  `display_name` VARCHAR(100) NULL,
  `auth_provider` VARCHAR(20) NOT NULL DEFAULT 'local'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `accounts` (
  `account_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `name` VARCHAR(100) NOT NULL,
  `account_type` VARCHAR(20) NOT NULL DEFAULT 'checking',
  `balance` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  `currency` VARCHAR(10) NOT NULL DEFAULT 'INR',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_accounts_user` (`user_id`),
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `categories` (
  `category_id` INT AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(50) NOT NULL UNIQUE,
  `icon` VARCHAR(10) DEFAULT '💰'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `expenses` (
  `expense_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NULL,
  `account_id` INT NULL,
  `amount` DECIMAL(10,2) NOT NULL,
  `category` VARCHAR(50) NOT NULL,
  `description` VARCHAR(255) NULL,
  `expense_date` DATE NOT NULL,
  `recurring_id` INT NULL,
  `created_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `fk_user` (`user_id`),
  INDEX `fk_expense_account` (`account_id`),
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `income` (
  `income_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NULL,
  `account_id` INT NULL,
  `amount` DECIMAL(10,2) NULL,
  `source` VARCHAR(50) NULL,
  `description` VARCHAR(255) NULL,
  `income_date` DATE NULL,
  INDEX `user_id` (`user_id`),
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `budgets` (
  `budget_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NULL,
  `category` VARCHAR(50) NULL DEFAULT 'Overall',
  `monthly_limit` DECIMAL(10,2) NOT NULL,
  `currency` VARCHAR(10) NOT NULL DEFAULT 'INR',
  `created_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE INDEX `idx_budgets_user_cat` (`user_id`, `category`),
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `savings_goals` (
  `goal_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NULL,
  `goal_name` VARCHAR(100) NULL,
  `target_amount` DECIMAL(10,2) NULL,
  `current_amount` DECIMAL(10,2) NULL,
  `target_date` DATE NULL,
  `description` TEXT NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'in_progress',
  `used_amount` DECIMAL(12,2) NOT NULL DEFAULT '0.00',
  `completed_at` DATETIME NULL,
  `archived_at` DATETIME NULL,
  `closed_at` DATETIME NULL,
  `icon` VARCHAR(10) NOT NULL DEFAULT '🎯',
  `color` VARCHAR(20) NOT NULL DEFAULT '#4edea3',
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `goal_contributions` (
  `contribution_id` INT AUTO_INCREMENT PRIMARY KEY,
  `goal_id` INT NOT NULL,
  `user_id` INT NOT NULL,
  `amount` DECIMAL(10,2) NOT NULL,
  `note` VARCHAR(255) NULL,
  `created_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_goal_contributions_goal` (`goal_id`, `created_at`),
  FOREIGN KEY (`goal_id`) REFERENCES `savings_goals` (`goal_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `settlements` (
  `settlement_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `peer_name` VARCHAR(100) NOT NULL,
  `amount` DECIMAL(10,2) NOT NULL,
  `status` VARCHAR(20) DEFAULT 'active',
  `note` VARCHAR(255) NULL,
  `txn_date` DATE NULL,
  `reason` VARCHAR(255) NULL,
  `balance_date` DATE NULL,
  `counts_as_expense` TINYINT(1) NOT NULL DEFAULT 0,
  `linked_expense_id` INT NULL,
  `created_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX `user_id` (`user_id`),
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `recurring_expenses` (
  `recurring_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT DEFAULT NULL,
  `title` VARCHAR(100) DEFAULT NULL,
  `amount` DECIMAL(10,2) DEFAULT NULL,
  `category` VARCHAR(50) DEFAULT NULL,
  `frequency` VARCHAR(20) DEFAULT NULL,
  `next_charge_date` DATE DEFAULT NULL,
  `icon` VARCHAR(10) DEFAULT '⚡',
  `status` VARCHAR(10) DEFAULT 'active',
  `recurring_type` VARCHAR(10) NOT NULL DEFAULT 'auto',
  `created_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `recurring_income` (
  `recurring_income_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `amount` DECIMAL(12,2) NOT NULL,
  `source` VARCHAR(50) NOT NULL DEFAULT 'Salary',
  `frequency` VARCHAR(20) NOT NULL DEFAULT 'Monthly',
  `next_pay_date` DATE NOT NULL,
  `icon` VARCHAR(10) DEFAULT '💼',
  `status` VARCHAR(10) NOT NULL DEFAULT 'active',
  `account_id` INT NULL,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_rec_inc_user` (`user_id`),
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `notifications` (
  `notification_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `icon` VARCHAR(10) NULL,
  `title` VARCHAR(255) NOT NULL,
  `message` VARCHAR(500) NULL,
  `link` VARCHAR(255) NULL,
  `is_read` TINYINT(1) NOT NULL DEFAULT 0,
  `dedup_key` VARCHAR(150) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_notifications_user_created` (`user_id`, `created_at`),
  UNIQUE INDEX `idx_notifications_user_dedup` (`user_id`, `dedup_key`),
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `notification_preferences` (
  `user_id` INT NOT NULL PRIMARY KEY,
  `budget_alerts` TINYINT(1) NOT NULL DEFAULT 1,
  `recurring_reminders` TINYINT(1) NOT NULL DEFAULT 1,
  `goal_milestones` TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `password_resets` (
  `reset_id` INT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `token_hash` VARCHAR(255) NOT NULL,
  `expires_at` DATETIME NOT NULL,
  `used_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_pw_resets_user` (`user_id`),
  INDEX `idx_pw_resets_hash` (`token_hash`),
  FOREIGN KEY (`user_id`) REFERENCES `users` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;
