-- 1. 데이터베이스 생성 및 선택 (기본 데이터베이스명이 memo_db인 경우)
CREATE DATABASE IF NOT EXISTS `memo_db` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `memo_db`;

-- --------------------------------------------------------
-- 2. folders (폴더 트리 구조 테이블)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS `folders` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name` VARCHAR(255) NOT NULL COMMENT '폴더 명칭',
    `parent_id` INT DEFAULT NULL COMMENT '상위 폴더 ID (상위가 없으면 NULL)',
    `color` VARCHAR(50) DEFAULT '' COMMENT '폴더 표시 색상 (Hex 코드)',
    `sort_order` INT DEFAULT 0 COMMENT '정렬 순서',
    FOREIGN KEY (`parent_id`) REFERENCES `folders`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------
-- 3. users (사용자 풀 관리 테이블)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS `users` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `username` VARCHAR(100) NOT NULL UNIQUE COMMENT '사용자 이름'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------
-- 4. memos (메인 문서 테이블 - LONGTEXT 무제한 확장)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS `memos` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `title` VARCHAR(255) NOT NULL COMMENT '문서 제목',
    `content` LONGTEXT COMMENT 'HTML 본문 (사진 포함 최대 4GB)',
    `tags` VARCHAR(255) DEFAULT '' COMMENT '쉼표 구분 태그 목록',
    `password` VARCHAR(255) DEFAULT '' COMMENT '보안 잠금 암호(난독화)',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '생성일시',
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '최종수정일시',
    `author` VARCHAR(100) DEFAULT '알 수 없음' COMMENT '최초 작성자',
    `last_editor` VARCHAR(100) DEFAULT '알 수 없음' COMMENT '마지막 편집자',
    `deleted_at` DATETIME DEFAULT NULL COMMENT '휴지통 이동 일시 (NULL이면 활성 문서)',
    `revision` INT DEFAULT 1 COMMENT '현재 리비전 버전 번호',
    `folder_id` INT DEFAULT 1 COMMENT '소속 폴더 ID',
    FOREIGN KEY (`folder_id`) REFERENCES `folders`(`id`) ON DELETE SET DEFAULT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------
-- 5. memo_history (문서 변경 이력 테이블 - 최근 10개 버전 보존)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS `memo_history` (
    `history_id` INT AUTO_INCREMENT PRIMARY KEY,
    `memo_id` INT NOT NULL COMMENT '원본 문서 고유번호',
    `title` VARCHAR(255) NOT NULL COMMENT '과거 버전 제목',
    `content` LONGTEXT COMMENT '과거 버전 HTML 본문',
    `tags` VARCHAR(255) DEFAULT '' COMMENT '과거 버전 태그',
    `saved_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '이력 백업 일시',
    `editor` VARCHAR(100) DEFAULT '알 수 없음' COMMENT '해당 버전을 수정한 기여자',
    `revision` INT DEFAULT 1 COMMENT '과거 리비전 번호',
    FOREIGN KEY (`memo_id`) REFERENCES `memos`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------
-- 6. settings (시스템 환경설정 및 MDM 중앙 통제 테이블)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS `settings` (
    `key` VARCHAR(100) PRIMARY KEY COMMENT '설정 식별자 (예약어 에러 방지)',
    `value` TEXT COMMENT '설정 값'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ========================================================
-- 7. 🚀 필수 인프라 초기화 데이터 데이터 (Seed)
-- ========================================================

-- 시스템 기본 폴더 강제 보존 (ID: 1 고정)
INSERT IGNORE INTO `folders` (`id`, `name`, `parent_id`, `color`, `sort_order`) 
VALUES (1, '기본 폴더', NULL, '', 0);

-- 마스터 관리자 세션 강제 확보
INSERT IGNORE INTO `users` (`id`, `username`) 
VALUES (1, '관리자');

-- 중앙 통제용 기본 시스템 파라미터 셋업
INSERT IGNORE INTO `settings` (`key`, `value`) VALUES ('current_user', '관리자');
INSERT IGNORE INTO `settings` (`key`, `value`) VALUES ('lock_timeout', '10');
INSERT IGNORE INTO `settings` (`key`, `value`) VALUES ('theme_mode', 'system');
INSERT IGNORE INTO `settings` (`key`, `value`) VALUES ('zoom_factor', '1.0');
